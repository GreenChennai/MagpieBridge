"""Configuration management for MagpieBridge."""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_default_config_path() -> Path:
    """Get default config path, handling PyInstaller bundle."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "config.json"
    return Path("config.json")


@dataclass
class OneBotConfig:
    http_host: str = "127.0.0.1"
    http_port: int = 3000
    ws_host: str = "127.0.0.1"
    ws_port: int = 3001
    access_token: str = ""


@dataclass
class HumanSimConfig:
    min_delay_ms: int = 200
    max_delay_ms: int = 1500
    keystroke_delay_ms: int = 50
    tremor_amplitude_px: float = 2.0
    bezier_steps: int = 20
    # --- Anti-detection: randomized click target (px radius) ---
    click_radius_px: int = 12
    # --- Anti-detection: move duration scaled by distance (ms) ---
    move_short_ms: int = 200
    move_long_ms: int = 800
    # --- Anti-detection: hover pause before clicking (ms) ---
    hover_pause_ms: int = 150
    # --- Anti-detection: thinking pause before typing (ms, normal distribution) ---
    think_delay_ms: int = 1200
    think_delay_std_ms: int = 500


@dataclass
class WechatConfig:
    window_position_x: int = 0
    window_position_y: int = 0
    window_width: int = 900
    window_height: int = 600
    adapter_version: str = "4.1.13"
    chat_list_x1: int = 70
    chat_list_y1: int = 70
    chat_list_x2: int = 335
    chat_list_y2: int = 580
    input_box_x: int = 580
    input_box_y: int = 515
    send_button_x: int = 835
    send_button_y: int = 545
    # --- 发送安全（v0.4.11 新增，默认全开：宁可漏发，绝不发错人）---
    # 发送前守卫：按 Enter 前复查聊天区标题是否仍是目标会话，不匹配则中止并清空输入框
    enable_target_guard: bool = True
    # 是否容忍 OCR 把长群名截断（候选是目标的前缀）。关掉后只认精确/高相似度
    allow_title_truncation: bool = True
    # 是否拒绝「同名+尾部编号」的变体（「客户群」vs「客户群2」）。长名加编号的
    # 相似度高达 0.95，只有这条规则能挡住，强烈建议保持开启
    reject_numbered_groups: bool = True
    # 按 Enter 后用发送按钮"绿→灰"验证是否真的发出去（无法判定时按已发送处理）
    verify_send_result: bool = True


@dataclass
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8080


@dataclass
class AdminConfig:
    enabled: bool = False
    admin_contacts: list[str] = field(default_factory=list)
    check_interval_sec: int = 5
    command_prefix: str = ""


@dataclass
class MonitorConfig:
    enabled: bool = False
    monitor_all: bool = True  # Monitor all non-muted chats by default
    poll_interval_sec: int = 3
    poll_interval_std_sec: int = 1
    max_unread_per_round: int = 2
    browse_min_sec: int = 20
    browse_max_sec: int = 45
    scroll_pages: int = 3
    react_delay_ms: int = 1200
    chat_x1: int = 350
    chat_y1: int = 60
    chat_x2: int = 885
    chat_y2: int = 545
    listen_contacts: list[str] = field(default_factory=list)

    # --- v0.4.0: smart continuous monitoring of active groups ---
    # Keep a chat window open and OCR it continuously (focus stays in the
    # browser), switching between the most active groups automatically.
    continuous_monitor: bool = True      # enable active-group OCR watcher
    continuous_check_interval: float = 2.0  # seconds between OCR polls
    active_session_count: int = 1        # how many groups to keep "open"/watch
    activity_decay: float = 0.85         # score decay each idle poll (0..1)
    activity_boost: float = 3.0          # score added per new unread message
    browser_focus_sec: int = 3          # how long to browse a page when idle
    idle_browse_pages_min: int = 1       # min pages to open when no tasks
    idle_browse_pages_max: int = 2       # max pages to open when no tasks
    browser_scroll: bool = True          # simulate human up/down scrolling
    browser_scroll_interval_sec: int = 6 # avg seconds between a scroll swipe
    browser_refresh_min_sec: int = 1800  # F5 every 30 minutes
    browser_refresh_max_sec: int = 3600  # ... up to 60 minutes
    browse_pause_hotkey: str = "ctrl+alt+s"  # global hotkey to pause/resume browsing
    browser_urls: list[str] = field(default_factory=lambda: [
        "https://www.baidu.com",
        "https://news.qq.com",
        "https://github.com/trending",
        "https://www.bilibili.com",
    ])

    @property
    def chat_region(self) -> tuple[int, int, int, int]:
        return (self.chat_x1, self.chat_y1, self.chat_x2, self.chat_y2)


@dataclass
class AlertConfig:
    """SMTP 邮件告警配置（发送失败 / 风控时提醒人工接管）。

    授权码不写死在代码里，由用户在后台填写并保存到 config.json。
    """
    enabled: bool = False          # 总开关：是否启用邮件告警
    smtp_host: str = "smtp.qq.com"  # 发送服务器（QQ 邮箱 smtp.qq.com）
    smtp_port: int = 465           # 端口（QQ 邮箱 SSL 465 / 587）
    use_ssl: bool = True           # 是否使用 SSL
    smtp_user: str = ""            # 登录账号 = 完整邮箱地址（如 xxx@qq.com）
    auth_code: str = ""            # 授权码（非 QQ 登录密码）
    receiver: str = ""             # 收件人邮箱（提醒发往哪）
    subject_prefix: str = "[MagpieBridge]"
    cooldown_sec: int = 300        # 冷却时间：N 秒内重复失败只发一封（防刷屏）
    test_mode: bool = False        # 内部：是否正在发测试邮件（仅影响日志）

    def is_usable(self) -> bool:
        """True when the alerter can actually send (enabled + 关键字段齐备)."""
        return bool(
            self.enabled
            and self.smtp_host
            and self.smtp_user
            and self.auth_code
            and self.receiver
        )

    def masked_auth_code(self) -> str:
        """打码后的授权码（用于后台回显，避免明文暴露）。"""
        if not self.auth_code:
            return ""
        if len(self.auth_code) <= 4:
            return "*" * len(self.auth_code)
        return "****" + self.auth_code[-4:]


@dataclass
class AppConfig:
    onebot: OneBotConfig = field(default_factory=OneBotConfig)
    human_sim: HumanSimConfig = field(default_factory=HumanSimConfig)
    wechat: WechatConfig = field(default_factory=WechatConfig)
    web: WebConfig = field(default_factory=WebConfig)
    admin: AdminConfig = field(default_factory=AdminConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    alert: AlertConfig = field(default_factory=AlertConfig)
    log_level: str = "INFO"
    log_file: str = "logs/magpie.log"

    @staticmethod
    def _filter_known(cls_dc, data: dict, section: str) -> dict:
        """只保留 dataclass 已知字段。

        旧版 `DataClass(**data)` 遇到任何未知键直接 TypeError，被外层 except
        吞掉后**整个配置静默回落默认值**（一个拼写错误 = 全部设置蒸发）。
        现在未知键仅告警忽略，已知键照常生效。
        """
        known = {f.name for f in fields(cls_dc)}
        unknown = set(data) - known
        if unknown:
            logger.warning("config 段 %s 含未知键（已忽略）: %s", section, sorted(unknown))
        return {k: v for k, v in data.items() if k in known}

    @classmethod
    def load(cls, path: Path | str | None = None) -> AppConfig:
        if path is None:
            path = _get_default_config_path()
        path = Path(path)
        if not path.exists():
            logger.info("Config file not found, using defaults")
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                onebot=OneBotConfig(**cls._filter_known(OneBotConfig, data.get("onebot", {}), "onebot")),
                human_sim=HumanSimConfig(**cls._filter_known(HumanSimConfig, data.get("human_sim", {}), "human_sim")),
                wechat=WechatConfig(**cls._filter_known(WechatConfig, data.get("wechat", {}), "wechat")),
                web=WebConfig(**cls._filter_known(WebConfig, data.get("web", {}), "web")),
                admin=AdminConfig(**cls._filter_known(AdminConfig, data.get("admin", {}), "admin")),
                monitor=MonitorConfig(**cls._filter_known(MonitorConfig, data.get("monitor", {}), "monitor")),
                alert=AlertConfig(**cls._filter_known(AlertConfig, data.get("alert", {}), "alert")),
                log_level=data.get("log_level", "INFO"),
                log_file=data.get("log_file", "logs/magpie.log"),
            )
        except Exception:
            logger.exception("Failed to load config, using defaults")
            return cls()

    def save(self, path: Path | str | None = None) -> None:
        if path is None:
            path = _get_default_config_path()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "onebot": asdict(self.onebot),
            "human_sim": asdict(self.human_sim),
            "wechat": asdict(self.wechat),
            "web": asdict(self.web),
            "admin": asdict(self.admin),
            "monitor": asdict(self.monitor),
            "alert": asdict(self.alert),
            "log_level": self.log_level,
            "log_file": self.log_file,
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Config saved to %s", path)
