"""ADR-0010 回归: 自愈去抖——用户发起 RDP 连接时绝不被 tscon 踢出。"""

from magpie.core import session_control as sc


class TestTripDebounce:
    def setup_method(self):
        sc._trip_streak = 0
        sc._null_fg_since = 0.0

    def test_transient_disconnect_does_not_hang(self, monkeypatch):
        """迁移窗口(1-2 次探针 Disconnected)绝不触发挂回。"""
        calls = []
        monkeypatch.setattr(sc, "hang_to_console",
                            lambda: calls.append(1) or (True, "ok"))
        WTS_DISCONNECTED = sc.WTS_DISCONNECTED
        # 第 1 次: Disconnected → 去抖(1/3)
        ok, _ = sc.ensure_session_active()
        assert not calls
        # 第 2 次: 仍 Disconnected → 去抖(2/3)
        ok, _ = sc.ensure_session_active()
        assert not calls
        # 第 3 次前用户连上了(Active + 有前台) → 计数清零, 不挂
        monkeypatch.setattr(sc, "probe_desktop",
                            lambda: {"session_state": 0, "input_desktop": True, "foreground": 999})
        ok, _ = sc.ensure_session_active()
        assert not calls

    def test_sustained_disconnect_hangs_after_debounce(self, monkeypatch):
        """真断开(持续 Disconnected)→ 第 3 次巡检自愈。"""
        calls = []
        monkeypatch.setattr(sc, "hang_to_console",
                            lambda: calls.append(1) or (True, "ok"))
        monkeypatch.setattr(sc, "probe_desktop",
                            lambda: {"session_state": sc.WTS_DISCONNECTED,
                                     "input_desktop": True, "foreground": 0})
        for i in range(2):
            sc.ensure_session_active()
        assert not calls
        sc.ensure_session_active()  # 第 3 次
        assert calls, "持续断开应在去抖后自愈"

    def test_recheck_cancels_when_recovered(self, monkeypatch):
        """去抖满足后 tscon 前复查: 若桌面已恢复则取消。"""
        calls = []
        monkeypatch.setattr(sc, "hang_to_console",
                            lambda: calls.append(1) or (True, "ok"))
        state = {"n": 0}

        def flaky_probe():
            state["n"] += 1
            if state["n"] <= 3:
                return {"session_state": sc.WTS_DISCONNECTED, "input_desktop": True, "foreground": 0}
            return {"session_state": 0, "input_desktop": True, "foreground": 123}

        monkeypatch.setattr(sc, "probe_desktop", flaky_probe)
        for _ in range(3):
            sc.ensure_session_active()
        assert not calls, "复查时已恢复 → 不应挂回"

    def test_force_bypasses_debounce(self, monkeypatch):
        calls = []
        monkeypatch.setattr(sc, "hang_to_console",
                            lambda: calls.append(1) or (True, "ok"))
        sc.ensure_session_active(force=True)
        assert calls
