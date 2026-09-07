"""会话自愈判定测试(ADR-0006):绝不踢正在使用的用户,绝不漏掉断开态。"""

from magpie.core.session_control import (
    NULL_FG_GRACE_SEC,
    WTS_ACTIVE,
    WTS_DISCONNECTED,
    should_hang,
)


class TestShouldHang:
    def test_active_with_foreground_never_hangs(self):
        """用户正在使用(RDP 活跃 + 有前台)→ 绝不挂起。"""
        ok, _ = should_hang(WTS_ACTIVE, True, 12345, 0.0, 1000.0)
        assert not ok

    def test_disconnected_hangs_immediately(self):
        """RDP 客户端关闭(Disconnected)→ 立即挂回控制台。"""
        ok, reason = should_hang(WTS_DISCONNECTED, True, 12345, 0.0, 1000.0)
        assert ok and "断开" in reason

    def test_locked_desktop_hangs(self):
        """输入桌面不可访问(锁屏)→ 挂回。"""
        ok, reason = should_hang(WTS_ACTIVE, False, 0, 0.0, 1000.0)
        assert ok and "锁屏" in reason

    def test_null_foreground_within_grace_never_hangs(self):
        """无前台但在宽限期内(RDP 最小化过渡态)→ 不挂,防止误踢。"""
        ok, _ = should_hang(WTS_ACTIVE, True, 0, 990.0, 1000.0)  # 才等 10s
        assert not ok

    def test_null_foreground_past_grace_hangs(self):
        """无前台持续超过宽限期 → 人已不看,挂回自愈。"""
        ok, reason = should_hang(
            WTS_ACTIVE, True, 0, 1000.0 - NULL_FG_GRACE_SEC - 5, 1000.0
        )
        assert ok and "无前台" in reason

    def test_null_foreground_first_observation_does_not_hang(self):
        """首次观察到无前台(null_fg_since=0)→ 只登记不触发。"""
        ok, reason = should_hang(WTS_ACTIVE, True, 0, 0.0, 1000.0)
        assert not ok and "宽限" in reason

    def test_unknown_session_state_with_foreground_ok(self):
        """探针读不到会话状态但桌面正常 → 不挂(保守,避免误动作)。"""
        ok, _ = should_hang(None, True, 12345, 0.0, 1000.0)
        assert not ok
