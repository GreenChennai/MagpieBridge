"""target_guard 回归测试：防发错人是全项目最高优先级不变量。"""

from magpie.core.target_guard import normalize, name_matches


class TestNormalize:
    def test_fullwidth_to_halfwidth(self):
        assert normalize("安信德＆创客龙") == normalize("安信德&创客龙")

    def test_member_count_suffix(self):
        assert normalize("测试群（12）") == normalize("测试群")
        assert normalize("测试群(12)") == normalize("测试群")

    def test_unread_suffix(self):
        assert normalize("测试群3条新消息") == normalize("测试群")

    def test_case_and_whitespace(self):
        assert normalize(" Test Group ") == "testgroup"


class TestNameMatches:
    def test_exact(self):
        assert name_matches("测试的佛山投流工作群", "测试的佛山投流工作群")

    def test_substring_is_never_a_match(self):
        # 子串匹配是发错人的头号来源，必须拒绝
        assert not name_matches("测试", "测试的佛山投流工作群")
        assert not name_matches("客户群", "大客户群")

    def test_numbered_variant_rejected(self):
        assert not name_matches("客户群2", "客户群")
        assert not name_matches("客户群", "客户群2")

    def test_ocr_truncation_prefix_ok(self):
        # OCR 把长名截断成前缀（尾巴短且无数字）→ 允许
        assert name_matches("测试的佛山投流工", "测试的佛山投流工作群")

    def test_short_names_require_exact(self):
        assert not name_matches("家", "家人")
        assert name_matches("家人", "家人")

    def test_reject_when_target_is_prefix_of_candidate(self):
        assert not name_matches("测试的佛山投流工作群备份", "测试的佛山投流工作群")

    def test_truncation_disabled(self):
        # allow_truncation=False：低相似度前缀不再兜底（短前缀 ratio 不足 0.85）
        assert not name_matches("测试的佛山", "测试的佛山投流工作群", allow_truncation=False)
        # 高相似度（长公共前缀）与精确匹配不受影响
        assert name_matches("测试的佛山投流工", "测试的佛山投流工作群", allow_truncation=False)
        assert name_matches("测试的佛山投流工作群", "测试的佛山投流工作群", allow_truncation=False)
