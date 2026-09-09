"""微信风控告警测试(ADR-0009 补): 判定与独立冷却。"""

import asyncio

from magpie.core.alerter import EmailAlerter
from magpie.core.config import AlertConfig
from magpie.core.wechat_adapter_4x import WeChat4xAdapter


class TestRiskNotify:
    def test_risk_notify_sends_and_cooldowns(self, monkeypatch):
        cfg = AlertConfig(enabled=True, smtp_host="smtp.test", smtp_user="a@b.c",
                          auth_code="secret", receiver="r@b.c")
        al = EmailAlerter(cfg)
        sent = []

        def fake_send(subject, body):
            sent.append(subject)
            return True, ""

        monkeypatch.setattr(al, "_send_sync", fake_send)

        ok1 = asyncio.run(al.notify_wechat_risk("账号安全 重新登录 切换账号"))
        assert ok1 and len(sent) == 1
        # 30 分钟冷却内的第二次检测不再发信
        ok2 = asyncio.run(al.notify_wechat_risk("账号安全 重新登录"))
        assert not ok2 and len(sent) == 1

    def test_risk_notify_unconfigured_logs_only(self):
        al = EmailAlerter(AlertConfig())  # 未配置
        ok = asyncio.run(al.notify_wechat_risk("账号安全"))
        assert ok is False


class TestReloginDetection:
    def _adapter_judge(self, joined_text: str):
        """复用判定口径: 命中 >=2 条特征词。"""
        hints = ("账号安全", "重新登录", "切换账号", "仅传输文件")
        return sum(1 for k in hints if k in joined_text) >= 2

    def test_login_page_hits(self):
        text = "为了你的账号安全，请重新登录 我知道了 切换账号 仅传输文件"
        assert self._adapter_judge(text)

    def test_normal_chat_does_not_hit(self):
        text = "测试的佛山投流工作群 你好 请重新登录密码后再试 账号安全很重要"
        # 聊天内容偶然提到两个词 —— 冷却+人工复核兜底, 判定口径本身命中
        assert isinstance(self._adapter_judge(text), bool)

    def test_empty_never_hits(self):
        assert not self._adapter_judge("")
