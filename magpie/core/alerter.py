"""SMTP 邮件告警器（发送失败 / 微信风控时提醒人工接管）。

设计要点：
- 纯标准库实现（smtplib + email），零第三方依赖，PyInstaller 打包友好。
- 授权码不写死：配置存 config.json（AlertConfig），由用户在后台填写。
- 异步发送：smtplib 是同步阻塞的，通过 asyncio.to_thread 放到线程池，
  避免阻塞事件循环（OneBot / web / 监听器共享同一个 loop）。
- 冷却 + 聚合：cooldown_sec 内重复失败不重复发信，而是累计次数，
  下一次真正发送时在正文中带上「期间累计失败 N 次」，防刷屏又不丢信息。
- 告警永远不抛出异常：发送失败只记日志，不影响主业务流程。
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import threading
import time
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Optional

from .config import AlertConfig

logger = logging.getLogger(__name__)


class EmailAlerter:
    """Send failure alerts via SMTP (e.g. QQ mail authorization code)."""

    def __init__(self, config: AlertConfig | None = None) -> None:
        self._config = config or AlertConfig()
        self._lock = threading.Lock()
        # 冷却聚合状态（线程安全，因为发送可能发生在任意线程）
        self._last_sent_at: float = 0.0
        self._pending_failures: int = 0
        self._last_notify_kind: str = ""
        self._last_notify_target: str = ""

    # ---------------------------------------------------------- config
    def update_config(self, config: AlertConfig) -> None:
        """热更新配置（后台保存后调用，无需重启）。"""
        with self._lock:
            self._config = config
        logger.info("邮件告警配置已更新（enabled=%s, smtp=%s, user=%s, receiver=%s）",
                    config.enabled, config.smtp_host, config.smtp_user, config.receiver)

    def is_configured(self) -> bool:
        return self._config.is_usable()

    @property
    def config(self) -> AlertConfig:
        return self._config

    # ---------------------------------------------------------- public API
    async def notify_send_failure(self, kind: str, target: str, detail: str = "") -> bool:
        """发送失败告警（异步）。kind: 文本/图片/@消息 等；target: 目标会话。

        内部处理冷却聚合：cooldown 窗口内的重复失败只累计，不重复发信。
        """
        if not self.is_configured():
            return False

        now = time.time()
        with self._lock:
            cooldown = float(self._config.cooldown_sec or 0)
            if cooldown > 0 and now - self._last_sent_at < cooldown and self._last_sent_at > 0:
                # 冷却期内：累计失败，等待窗口结束由下一次触发合并发送
                self._pending_failures += 1
                self._last_notify_kind = kind
                self._last_notify_target = target
                logger.info("邮件告警冷却中（%ds 内），已累计 %d 次失败待合并",
                            int(cooldown - (now - self._last_sent_at)), self._pending_failures)
                return False
            pending = self._pending_failures
            self._pending_failures = 0
            self._last_sent_at = now
            self._last_notify_kind = kind
            self._last_notify_target = target

        subject, body = self._build_message(kind, target, detail, pending)
        ok, err = await asyncio.to_thread(self._send_sync, subject, body)
        if ok:
            logger.warning("已发送邮件告警: %s", subject)
        else:
            logger.error("邮件告警发送失败: %s", err)
            # 发送失败要释放冷却窗口，否则后续告警被永久吞掉
            with self._lock:
                if self._last_sent_at == now:
                    self._last_sent_at = 0.0
        return ok

    async def send_test(self) -> tuple[bool, str]:
        """发送测试邮件（后台「测试」按钮）。返回 (成功, 提示信息)。

        注意：必须使用 _send_sync 的真实返回值，否则配置错误时会误报「已发送」，
        用户以为配置成功，实际永远收不到告警邮件。
        """
        cfg = self._config
        missing = self.missing_fields()
        if missing:
            return False, f"请先填写：{'、'.join(missing)}"

        subject = f"{cfg.subject_prefix or '[MagpieBridge]'} 测试邮件"
        body = (
            "这是一封来自 MagpieBridge 的测试邮件。\n\n"
            "邮件提醒功能配置正确，当微信消息发送失败或触发风控时，"
            "将自动发送告警邮件提醒人工接管。\n\n"
            f"发件人: {cfg.smtp_user}\n"
            f"收件人: {cfg.receiver}\n"
            f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        ok, err = await asyncio.to_thread(self._send_sync, subject, body, test=True)
        if not ok:
            return False, f"测试邮件发送失败：{err}"

        msg = "测试邮件已发送，请查收"
        if not cfg.enabled:
            msg += "（注意：当前「启用邮件提醒」开关未打开，实际发送失败时不会自动告警）"
        return True, msg

    def missing_fields(self) -> list[str]:
        """返回尚未填写的必填字段（中文名），供后台精确提示用户缺哪一项。"""
        cfg = self._config
        missing: list[str] = []
        if not (cfg.smtp_host or "").strip():
            missing.append("SMTP 服务器")
        if not (cfg.smtp_user or "").strip():
            missing.append("发件邮箱")
        if not (cfg.auth_code or "").strip():
            missing.append("授权码")
        if not (cfg.receiver or "").strip():
            missing.append("收件邮箱")
        return missing

    # ---------------------------------------------------------- internals
    def _build_message(self, kind: str, target: str, detail: str, pending: int) -> tuple[str, str]:
        cfg = self._config
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        subject = f"{cfg.subject_prefix or '[MagpieBridge]'} 发送失败告警 - 需要人工接管"
        lines = [
            "MagpieBridge 告警：微信消息发送失败，可能需要人工接管！",
            "",
            f"时间: {ts}",
            f"类型: {kind}",
            f"目标: {target or '(未知)'}",
        ]
        if detail:
            lines.append(f"详情: {detail}")
        if pending > 0:
            lines.append(f"（冷却期内另有 {pending} 次失败已合并，不再单独发信）")
        lines += [
            "",
            "建议排查:",
            "1. 微信是否被风控 —— 查看发送按钮是否仍为绿色 / 是否出现限制提示；",
            "2. 微信窗口是否正常打开、未被最小化或遮挡；",
            "3. 若为风控，请人工登录微信处理（可能需要完成验证）。",
            "",
            "本邮件由 MagpieBridge 自动发送，请勿直接回复。",
        ]
        return subject, "\n".join(lines)

    def _send_sync(self, subject: str, body: str, test: bool = False) -> tuple[bool, str]:
        """同步发送（在线程池中执行）。

        返回 (是否成功, 错误说明)；成功时错误说明为空串。绝不向上抛出异常，
        保证告警逻辑永远不影响主业务流程。
        """
        cfg = self._config
        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = Header(subject, "utf-8")
            msg["From"] = formataddr((str(Header("MagpieBridge 告警", "utf-8")), cfg.smtp_user))
            msg["To"] = cfg.receiver
            msg["Date"] = time.strftime("%a, %d %b %Y %H:%M:%S %z")

            timeout = 15  # 秒；QQ SMTP 正常很快，给足余量
            if cfg.use_ssl:
                # smtplib.SMTP_SSL 默认就用 SMTP 端口（465 为 SSL 专用端口）
                with smtplib.SMTP_SSL(cfg.smtp_host, int(cfg.smtp_port), timeout=timeout) as server:
                    server.login(cfg.smtp_user, cfg.auth_code)
                    server.sendmail(cfg.smtp_user, [cfg.receiver], msg.as_string())
            else:
                # 非 SSL（587 STARTTLS）或 25
                with smtplib.SMTP(cfg.smtp_host, int(cfg.smtp_port), timeout=timeout) as server:
                    server.ehlo()
                    if int(cfg.smtp_port) == 587:
                        server.starttls()
                        server.ehlo()
                    server.login(cfg.smtp_user, cfg.auth_code)
                    server.sendmail(cfg.smtp_user, [cfg.receiver], msg.as_string())
            if test:
                logger.info("测试邮件发送成功 -> %s", cfg.receiver)
            return True, ""
        except smtplib.SMTPAuthenticationError as e:
            # QQ 邮箱 535：授权码错误 / 过期 / 用的是 QQ 登录密码 / 未开启 SMTP 服务
            code = getattr(e, "smtp_code", None)
            hint = "授权码错误或未开启 SMTP 服务（请确认填的是「授权码」而不是 QQ 登录密码；"
            hint += " QQ 邮箱需先在 设置 → 账号 中开启 IMAP/SMTP 服务并生成授权码）"
            logger.error("SMTP 登录失败(%s)（授权码错误？）: %s", code, e)
            return False, f"{code or ''} {hint}".strip()
        except smtplib.SMTPConnectError as e:
            hint = f"无法连接 SMTP 服务器 {cfg.smtp_host}:{cfg.smtp_port}（请检查服务器地址与端口是否匹配）"
            logger.error("SMTP 连接失败: %s", e)
            return False, hint
        except smtplib.SMTPServerDisconnected as e:
            # 典型：465 端口用了非 SSL，或 587 端口未走 STARTTLS
            hint = (
                f"服务器主动断开连接（端口 {cfg.smtp_port} 与「使用 SSL」设置不匹配："
                "465 需勾选 SSL，587 不要勾选 SSL）"
            )
            logger.error("SMTP 连接被断开: %s", e)
            return False, hint
        except smtplib.SMTPRecipientsRefused as e:
            logger.error("收件人被拒绝: %s", e)
            return False, f"收件邮箱被服务器拒绝（请检查收件邮箱地址是否正确）: {e.recipients}"
        except smtplib.SMTPSenderRefused as e:
            logger.error("发件人被拒绝: %s", e)
            return False, f"发件邮箱被服务器拒绝（请检查发件邮箱地址是否正确）"
        except smtplib.SMTPException as e:
            logger.error("SMTP 发送失败: %s", e)
            return False, f"SMTP 错误: {e}"
        except TimeoutError:
            logger.error("SMTP 连接超时: %s:%s", cfg.smtp_host, cfg.smtp_port)
            return False, f"连接超时（{cfg.smtp_host}:{cfg.smtp_port}）—— 请检查网络或防火墙是否拦截"
        except OSError as e:
            # 连接被拒 / DNS 解析失败 / 端口不通 等底层网络错误
            logger.error("SMTP 网络错误: %s", e)
            return False, f"网络错误（{cfg.smtp_host}:{cfg.smtp_port}）: {e}"
        except Exception as e:
            logger.error("邮件发送异常: %s", e)
            return False, f"未知错误: {e}"


# ---------------------------------------------------------------- 全局单例
_alerter: Optional[EmailAlerter] = None
_alerter_lock = threading.Lock()


def get_alerter() -> EmailAlerter:
    """Get the global alerter (created lazily with defaults)."""
    global _alerter
    if _alerter is None:
        with _alerter_lock:
            if _alerter is None:
                _alerter = EmailAlerter()
    return _alerter


def set_alerter(alerter: EmailAlerter) -> None:
    """Set the global alerter (called once at startup from main.py)."""
    global _alerter
    with _alerter_lock:
        _alerter = alerter
