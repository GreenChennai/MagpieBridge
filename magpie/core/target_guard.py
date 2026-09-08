"""发送目标守卫（target guard）—— 防止"发错人"的最后一公里校验。

设计原则
--------
**宁可漏发，绝不发错人。**

历史上这里出问题的方式是"模糊匹配放得太宽"：
``target in item_name or item_name in target`` 这种**双向子串**匹配会让
目标「客户群」命中「客户群2」，目标「张三」命中「张三丰」—— 而返回的是
列表里**第一个**命中项，于是消息稳定地发给了错误的人。

本模块把匹配规则收紧为四档，并且刻意**禁止**子串扩展：

1. **精确**（归一化后相等）—— 唯一可信的匹配，直接通过。
2. **截断前缀** —— 候选是目标的前缀（OCR 把长群名截断成「安信德&创客龙客...」）。
   启用条件很苛刻：候选 ≥3 字、候选长度 ≥ 目标的 60%、被截掉的尾巴 ≤4 字
   **且尾巴里不含数字**。最后一条是防「客户群」⊂「客户群2」这类编号群的关键。
3. **方向性拒绝** —— 目标是候选的前缀（「客户群」⊂「客户群2」）→ **明确 False**。
   这是本模块存在的核心理由，宁可判定失败也不能放行。
4. **相似度** —— 仅当目标名 ≥4 字时才启用，阈值 0.85（旧代码是 0.6，
   「客户群」vs「客户群2」能算到 0.857 从而误放行）。

短名（<4 字）永远走"精确或截断前缀"，不接受相似度兜底：
两个字的中文名只要错一个字，语义上就是另一个人了。

已知残余风险（权衡后接受）
--------------------------
若同时存在「安信德&创客龙」与「安信德&创客龙客户群」两个会话，且 OCR 把
聊天区标题截断成「安信德&创客龙」，截断前缀规则会放行。缓解手段有两条：
列表定位走 :func:`find_unique`，多个会话同时模糊命中时判定歧义并拒绝发送；
若仍需彻底关闭，传 ``allow_truncation=False`` 给 :func:`name_matches`
（或配置 ``wechat.allow_title_truncation = false``）。

所有函数都是纯函数（不 import adapter / 不碰 UI），方便单元测试与在
``ocr.py``、``wechat_adapter_4x.py``、``listener.py`` 三处复用同一套口径。
"""

from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from typing import Iterable, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# 短名阈值：目标名短于该长度时，不允许用相似度兜底（只允许精确/截断前缀）
SHORT_NAME_LEN = 4
# 相似度阈值（旧值 0.6 会让「客户群」误命中「客户群2」= 0.857）
SIMILARITY_THRESHOLD = 0.85
# 截断前缀匹配时，候选至少要达到目标长度的这个比例
PREFIX_MIN_RATIO = 0.6
# 截断前缀匹配的绝对最短长度（防止「王」匹配「王小明」）
PREFIX_MIN_ABS = 3
# 被 OCR 截掉的最大尾巴长度；超出说明这根本是另一个名字
PREFIX_MAX_TAIL = 4

# 群名尾部的成员数，如「客户群(12)」「客户群（12）」
_MEMBER_COUNT_RE = re.compile(r"[（(]\s*\d+\s*[)）]\s*$")
# 未读数后缀，如「张三 3条新消息」
_UNREAD_SUFFIX_RE = re.compile(r"\d+\s*条新消息\s*$")
# 粘连在名称块尾部的会话时间/截断省略号。PP-OCR 常把列表右侧的时间与名称识别进
# 同一个文本块，且形态多变（「测试的佛山投流工..昨天22:48」「群.．昨天2:34」——
# 中英文点号混排、"昨天"+时间成对出现），必须**循环**剥到尾巴稳定为止。
# 目标名走同一 normalize，剥掉的东西两边一致，不会产生误配对。
_TAIL_TOKEN_RE = re.compile(
    r"(?:上午|下午|晚上|中午|凌晨|昨天|今日|今天|前天|昨|今|"
    r"星期[一二三四五六日天]|周[一二三四五六日天]|"
    r"\d{1,2}[:：]\d{2}|[.．·。…\s])+$"
)
# 省略号（OCR 截断长名时的常见产物）
_ELLIPSIS_RE = re.compile(r"(\.{2,}|…+)+\s*$")
# 尾部编号，如「客户群2」「测试群10」
_TRAILING_DIGITS_RE = re.compile(r"\d+$")


def normalize(name: str) -> str:
    """归一化会话名，用于严格比对。

    依次做：NFKC（全角→半角，如 ＆→& 、（）→()）→ 去成员数后缀 →
    去未读数后缀 → 去尾部省略号 → 去掉所有空白 → 转小写。
    """
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", str(name))
    s = _MEMBER_COUNT_RE.sub("", s)
    s = _UNREAD_SUFFIX_RE.sub("", s)
    # 循环剥离尾部时间/点号 token（「..昨天22:48」这类多重尾巴一次剥不干净）
    prev = None
    while prev != s:
        prev = s
        s = _TAIL_TOKEN_RE.sub("", s)
    s = re.sub(r"\s+", "", s)
    return s.strip().lower()


def _strip_trailing_digits(s: str) -> str:
    """去掉尾部编号：「客户群2」→「客户群」，用于识别同名编号群。"""
    return _TRAILING_DIGITS_RE.sub("", s)


def _is_numbered_variant(c: str, t: str) -> bool:
    """两个名字是否只差一个尾部编号（「客户群」vs「客户群2」）。

    长名 + 编号后缀用相似度也挡不住：「安信德创客龙客户群」vs
    「安信德创客龙客户群2」的 ratio 高达 0.952，远超 0.85 阈值 ——
    必须单独拦一道，否则编号群之间会互相串发。
    """
    return _strip_trailing_digits(c) == _strip_trailing_digits(t)


def _truncation_ok(c: str, t: str) -> bool:
    """候选 c 是否可以被认定为"目标 t 被 OCR 截断后的样子"。

    ``c`` 与 ``t`` 均为已归一化的字符串，且 ``t.startswith(c)`` 成立。

    防误判的三道闸：
    * 尾巴里含数字 → 说明是「客户群2」这类**编号群**，不是截断，拒绝；
    * 尾巴超过 PREFIX_MAX_TAIL → 两个名字差异太大，是另一个会话，拒绝；
    * 候选太短（<3 字 或 < 目标的 60%）→ 判据不足，拒绝。
    """
    tail = t[len(c):]
    if any(ch.isdigit() for ch in tail):
        return False
    if len(tail) > PREFIX_MAX_TAIL:
        return False
    if len(c) < PREFIX_MIN_ABS:
        return False
    return len(c) >= len(t) * PREFIX_MIN_RATIO


def name_matches(
    candidate: str,
    target: str,
    allow_truncation: bool = True,
    reject_numbered: bool = True,
) -> bool:
    """候选名是否可以安全地认定为同一个会话。

    见模块 docstring 的四档规则。返回 True 表示"可以发"，False 表示
    "不敢发"——包括所有拿不准的情况。

    Args:
        allow_truncation: 是否容忍 OCR 截断（候选是目标的前缀）。关闭后
            只接受精确匹配与高相似度，最安全但漏发率会上升。
        reject_numbered: 是否拒绝「同名 + 尾部编号」的变体。长名加编号
            的相似度极高（0.95+），只有这条规则能挡住，建议保持开启。
    """
    c = normalize(candidate)
    t = normalize(target)
    if not c or not t:
        return False

    if c == t:
        return True

    # 同名编号群：唯一区别是尾部编号 → 一律拒绝（长名相似度挡不住）
    if reject_numbered and _is_numbered_variant(c, t):
        return False

    # OCR 截断：候选是目标的前缀，且尾巴特征符合"被切掉一截"
    if allow_truncation and t.startswith(c) and _truncation_ok(c, t):
        return True

    # ⚠️ 不设「尾部截断兜底」(2026-09-08 现场教训): 微信列表里可能同时存在
    # 真实的同尾短群 —— 「测试的佛山投流工作群」与独立的「佛山投流工作群」
    # 并存, 尾部规则会把消息发进错误的群。目标群找不到时的正解是展开折叠
    # 置顶聊天重扫(见 wechat_adapter_4x.search_contact)。

    # 方向性拒绝：目标是候选的前缀 —— 「客户群」⊂「客户群2」，绝不放行
    if c.startswith(t):
        return False

    # 短名不接受相似度兜底，只认精确/截断前缀（上面已处理）
    if len(t) < SHORT_NAME_LEN or len(c) < SHORT_NAME_LEN:
        return False

    return difflib.SequenceMatcher(None, c, t).ratio() >= SIMILARITY_THRESHOLD


def match_reason(candidate: str, target: str, allow_truncation: bool = True) -> str:
    """返回匹配结论的可读理由（用于日志/审计），与 name_matches 同口径。"""
    c = normalize(candidate)
    t = normalize(target)
    if not c or not t:
        return "空名"
    if c == t:
        return "精确匹配"
    if _is_numbered_variant(c, t):
        return f"同名编号群，拒绝({c} vs {t})"
    if t.startswith(c):
        if not allow_truncation:
            return f"已关闭截断容忍({c}⊂{t})"
        if _truncation_ok(c, t):
            return f"截断前缀({c}⊂{t})"
        return f"前缀但尾巴可疑，拒绝({c}⊂{t}，尾={t[len(c):]})"
    if c.startswith(t):
        return f"目标为候选前缀，拒绝({t}⊂{c})"
    if len(t) < SHORT_NAME_LEN or len(c) < SHORT_NAME_LEN:
        return f"短名不启用相似度({c} vs {t})"
    ratio = difflib.SequenceMatcher(None, c, t).ratio()
    if ratio >= SIMILARITY_THRESHOLD:
        return f"相似度{ratio:.3f}"
    return f"相似度不足{ratio:.3f}<{SIMILARITY_THRESHOLD}"


def find_unique(
    names: Sequence[str] | Iterable[str],
    target: str,
    *,
    allow_fuzzy: bool = True,
    allow_truncation: bool = True,
) -> Tuple[Optional[str], str]:
    """在一批候选名中挑出**唯一可信**的目标名。

    Returns:
        (matched_name, reason)。匹配不到或有歧义时 matched_name 为 None。

    歧义处理：模糊档命中多项时返回 None 并在 reason 里列出全部候选。
    与其赌一个，不如不发送 ——  caller 看到 None 应当放弃本次发送。
    """
    names = list(names or [])
    t = normalize(target)
    if not t or not names:
        return None, "空目标或空候选"

    exact = [n for n in names if normalize(n) == t]
    if exact:
        if len(exact) > 1:
            # 同名会话（罕见）：取第一个，但记一笔，避免静默
            logger.warning("目标「%s」精确匹配到 %d 个同名会话，取第一个", target, len(exact))
        return exact[0], "精确匹配"

    if not allow_fuzzy:
        return None, f"严格模式：无精确匹配（候选 {len(names)} 项）"

    fuzzy = [n for n in names if name_matches(n, target, allow_truncation=allow_truncation)]
    if not fuzzy:
        best = max(names, key=lambda n: difflib.SequenceMatcher(None, normalize(n), t).ratio())
        return None, f"无匹配（最接近「{best}」 {match_reason(best, target, allow_truncation)}）"

    if len(fuzzy) > 1:
        # 歧义：例如「客户群」与「客户群2」同时模糊命中 → 拒绝发送
        preview = "、".join(fuzzy[:5])
        return None, f"歧义：{len(fuzzy)} 个会话同时模糊匹配（{preview}），拒绝发送"

    return fuzzy[0], match_reason(fuzzy[0], target, allow_truncation)


