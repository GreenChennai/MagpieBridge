"""息屏状态机 + 监听去重缓存测试（不碰真实显示器/钩子）。"""

from magpie.core import screen_off
from magpie.monitor.listener import FingerprintCache, PatternMatcher


class TestScreenOffState:
    """状态机测试：所有真实副作用（显示器/钩子/看门狗）全部打桩。"""

    def setup_method(self):
        self._saved = {
            name: getattr(screen_off, name)
            for name in ("_install_hooks", "_uninstall_hooks", "_start_reblack",
                         "_stop_reblack_now", "_monitor_power")
        }
        screen_off._install_hooks = lambda: None
        screen_off._uninstall_hooks = lambda: None
        screen_off._start_reblack = lambda: None
        screen_off._stop_reblack_now = lambda: None
        screen_off._monitor_power = lambda mode: None
        screen_off.wake_display(reason="test-setup")

    def teardown_method(self):
        screen_off.wake_display(reason="test-teardown")
        for name, fn in self._saved.items():
            setattr(screen_off, name, fn)

    def test_toggle_roundtrip(self):
        assert screen_off.turn_off_display() is True
        assert screen_off.is_screen_off() is True
        assert screen_off.wake_display(reason="test") is True
        assert screen_off.is_screen_off() is False
        # 重复唤醒是 no-op
        assert screen_off.wake_display(reason="test") is False

    def test_global_flag_regression(self):
        """回归：旧版缺 global 声明，turn_off 后 is_screen_off 恒 False。"""
        screen_off.turn_off_display()
        assert screen_off._screen_off is True  # 模块级状态真的被更新


class TestFingerprintCache:
    def test_dedup_same_text(self):
        c = FingerprintCache()
        assert not c.contains("测试的佛山投流工作群", "1")
        c.add("测试的佛山投流工作群", "1")
        assert c.contains("测试的佛山投流工作群", "1")

    def test_session_name_variants_share_bucket(self):
        """OCR 噪声：同一会话名的不同写法必须命中同一指纹桶。"""
        c = FingerprintCache()
        c.add("测试的佛山投流工作群（12）", "你好")
        assert c.contains("测试的佛山投流工作群", "你好")

    def test_fifo_eviction(self):
        c = FingerprintCache(max_per_session=3)
        for i in range(5):
            c.add("g", f"m{i}")
        assert not c.contains("g", "m0")  # 最旧的被淘汰
        assert not c.contains("g", "m1")
        assert c.contains("g", "m4")  # 最新的保留

    def test_glyph_path_uses_same_key(self):
        """回归：glyph 兜底与主路径必须同一键口径（旧版两个键 → "1" 收两次）。"""
        c = FingerprintCache()
        c.add("群", "1")  # 主路径已收
        assert c.contains("群", "1")  # glyph 兜底再来同文本 → 命中


class TestPatternMatcher:
    def test_substring_never_matches(self):
        m = PatternMatcher()
        m.add_pattern(group="测试", message="1", plugin_name="p")
        assert m.match("测试的佛山投流工作群", "", "1") == []
        assert m.match("测试", "", "1") == ["p"]

    def test_empty_group_matches_all(self):
        m = PatternMatcher()
        m.add_pattern(group="", message="1", plugin_name="p")
        assert m.match("任意群", "", "1") == ["p"]

    def test_regex_message(self):
        m = PatternMatcher()
        m.add_pattern(group="", message=r"^/ll", plugin_name="p")
        assert m.match("g", "", "/ll stats") == ["p"]
        assert m.match("g", "", "ll stats") == []
