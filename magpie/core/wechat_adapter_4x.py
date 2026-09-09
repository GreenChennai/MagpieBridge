"""WeChat 4.x adapter - OCR-based implementation for Flutter/XAML UI."""

from __future__ import annotations

import ctypes
import difflib
import io
import logging
import random
import re
import time
from typing import Optional

from PIL import Image

from . import capture
from .audit import audit
from .clipboard import Clipboard
from .config import WechatConfig
from .human_simulator import HumanSimulator
from .ocr import (
    ChatListItem,
    _is_noise_symbol,
    _merge_name_blocks,
    find_contact_by_name,
    ocr_recognize,
    scan_chat_list,
)
from .target_guard import match_reason, name_matches, normalize
from .wechat_adapter import WeChatAdapter
from .window_manager import WindowManager


def _get_file_version(file_path: str) -> Optional[str]:
    """Read the FileVersion of an exe via VerQueryValue (ctypes, version.dll)."""
    try:
        import ctypes.wintypes as wintypes
        version = ctypes.windll.version  # version.dll hosts the *Version* APIs
        size = version.GetFileVersionInfoSizeW(file_path, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(file_path, 0, size, buf):
            return None
        ver_ptr = ctypes.c_void_p()
        ver_len = wintypes.UINT()
        ok = version.VerQueryValueW(
            buf, "\\", ctypes.byref(ver_ptr), ctypes.byref(ver_len)
        )
        if not ok:
            return None
        # VS_FIXEDFILEINFO: dwFileVersionMS at dword[2], dwFileVersionLS at dword[3]
        fixed = ctypes.cast(ver_ptr, ctypes.POINTER(wintypes.DWORD))
        ms = fixed[2]
        ls = fixed[3]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return None

logger = logging.getLogger(__name__)


def _norm_id(s: str) -> str:
    """@ 成员标识归一化: 去空格/下划线/@符号/省略号, 转小写。

    微信里"群名片"与"成员显示名"常不一致("Green_Chennai" vs "Green Chennai"),
    面板候选还可能带截断省略号 —— 比较前必须抹掉这些差异。
    """
    return re.sub(r"[\s_@＠….·]+", "", s or "").lower()

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32


class WeChat4xAdapter(WeChatAdapter):
    """WeChat 4.1.13 (64-bit) adapter using OCR for text recognition."""

    def __init__(self, human_sim: HumanSimulator | None = None, config: WechatConfig | None = None) -> None:
        self._human = human_sim or HumanSimulator()
        self._clipboard = Clipboard()
        self._window = WindowManager()
        self._config = config or WechatConfig()
        self._chat_list_region: Optional[tuple[int, int, int, int]] = None
        self._window_rect: Optional[tuple[int, int, int, int]] = None
        self._baseline_items: list[ChatListItem] = []
        self._baseline_scanned = False
        self._last_visible_names: set[str] = set()
        # 最近一次 search_contact() 成功锁定的目标会话名。发送前守卫
        # （_assert_target）会拿它复查当前聊天区标题，防止中途被切走。
        self._pending_target: Optional[str] = None
        self._pending_target_since: float = 0.0
        self._hidden_warned = False

    @property
    def version(self) -> str:
        return "4.1.13"

    def _update_window_rect(self) -> bool:
        hwnd = self._window.find_window()
        if not hwnd:
            return False
        rect = self._window.get_window_rect()
        if not rect:
            return False
        self._window_rect = rect
        return True

    def _log_hidden_once(self) -> None:
        if not self._hidden_warned:
            self._hidden_warned = True
            logger.error("微信窗口处于隐藏/托盘状态：本次不做任何截图（对隐藏 Qt 窗口反复 "
                         "PrintWindow 会把微信渲染管线拖死，表现为界面卡死需重启微信）")
        else:
            logger.debug("微信窗口仍隐藏，跳过截图")

    def _grab_screen_region(self, x: int, y: int, w: int, h: int) -> Optional[Image.Image]:
        return capture.grab_screen_region(x, y, w, h)

    def _grab_printwindow(self, hwnd: int, w: int, h: int) -> Optional[Image.Image]:
        return capture.grab_printwindow(hwnd, w, h)

    def _get_screenshot(self) -> Optional[Image.Image]:
        """Grab the WeChat window pixels.

        主路径改为 **屏幕矩形 BitBlt**（v0.4.17）：
        - 不进入微信进程 —— 对隐藏/托盘状态的 Qt 窗口高频 PrintWindow 会把微信
          UI 渲染管线拖死（"发消息偶发整个微信界面卡死、必须完全重启"的根因）；
        - 屏幕抓取天然包含微信的独立弹层（@ 候选面板等），而
          PrintWindow(主窗口) 根本不渲染这些弹层，导致 @ 候选 OCR 读到的永远是
          底下的聊天消息行。
        窗口被其他程序整个盖住时才退回 PrintWindow 兜底；窗口隐藏则直接返回
        None，让上层守卫快速失败，绝不盲动 UI。
        """
        try:
            hwnd = self._window.find_window()
            if not hwnd:
                return None
            if not user32.IsWindowVisible(hwnd):
                self._log_hidden_once()
                return None
            rect = self._window.get_window_rect()
            if not rect:
                return None
            x, y, w, h = rect
            if w < 50 or h < 50:
                return None
            self._hidden_warned = False

            # 中心点被谁占着：是微信自己(或其弹层)→ 屏幕直采；否则 PrintWindow 兜底
            unoccluded = not capture.is_window_occluded(hwnd, x, y, w, h)

            t0 = time.perf_counter()
            img = self._grab_screen_region(x, y, w, h) if unoccluded else None
            if img is None:
                img = self._grab_printwindow(hwnd, w, h)
            dt = time.perf_counter() - t0
            if dt > 1.5:
                logger.warning("截图耗时 %.1fs（微信渲染可能已迟滞），mode=%s",
                               dt, "screen" if unoccluded else "printwindow")
            return img
        except Exception:
            logger.exception("Failed to capture screenshot")
            return None

    def _to_screen(self, x: int, y: int) -> tuple[int, int]:
        """Convert window-client coords (from OCR screenshot) to screen coords."""
        hwnd = self._window.find_window()
        if not hwnd:
            return x, y
        pt = ctypes.wintypes.POINT(x, y)
        user32.ClientToScreen(hwnd, ctypes.byref(pt))
        return pt.x, pt.y

    def find_chat_list(self) -> bool:
        if not self._update_window_rect():
            logger.error("未找到微信窗口")
            return False
        self._chat_list_region = (
            self._config.chat_list_x1,
            self._config.chat_list_y1,
            self._config.chat_list_x2,
            self._config.chat_list_y2,
        )
        logger.debug("聊天列表区域: %s", self._chat_list_region)
        return True

    def has_chat_list(self) -> bool:
        """Cheap check: chat list region already located (no window scan)."""
        return self._chat_list_region is not None

    def _chat_title(self) -> str:
        """OCR the chat panel title (active chat name), may return ''.

        WeChat 4.x renders the active chat name at the top of the chat panel.
        Extended region to capture long group names (up to the icons on the right).

        Picks the TOP-MOST recognisable line (smallest y) instead of the first
        OCR result: the region can also catch leftovers from the row below, and
        the old "first result wins" behaviour made the title non-deterministic
        (which in turn made the send-guard unreliable).

        The top-most line is then rebuilt from ALL of its OCR text boxes
        (x-sorted, overlap-dedup) — PP-OCR frequently splits a name like
        "安信德＆创客龙 金陵" into two boxes ("安信德" + "德＆创客龙 金陵"), and
        keeping only the best single box would truncate the title and make the
        pre-send guard (``_assert_target``) reject the correct chat.

        Single-character contact names (e.g. "爸" / "妈" / "姐") are a known
        pain point: PP-OCR's text_score is already 0.35, but a lone short
        Chinese glyph with no linguistic context still scores 0.40-0.50. The
        old 0.5 hard cutoff discarded them, so the title came back empty and
        the send-guard rejected the send. On other frames the detector drops
        the glyph entirely and returns only the UI divider as "-", which the
        guard would mistake for a chat named "-". We:
          * drop the score floor to 0.30 (still above the engine's internal
            text_score=0.35 noise floor),
          * drop pure-symbol/punctuation blocks ("-", "—", "·", "..", etc.)
            so the divider can never masquerade as a name.
        The downstream ``_assert_target`` does exact-name matching via
        :func:`target_guard.name_matches`, so any false recall from lowering
        the threshold is still caught downstream by the strict equality check.
        """
        shot = self._get_screenshot()
        if not shot:
            return ""
        try:
            # 标题区裁剪。左缘 336(聊天面板起点): RDP→控制台切换后字体渲染
            # 会横向偏移 1-3px, 裁剪过贴会让首字(如「测」)只剩半个 → OCR 读成
            # 「则」/漏字(ADR-0009, 用户实测「测试的佛山投流工作群」→
            # 「则试的…/试的…」)。首次识别偏短时用左扩+2x 放大二次识别兜底。
            texts = ocr_recognize(shot.crop((336, 12, 800, 82)))

            # Keep only confident, non-empty, non-noise blocks; group into
            # visual rows (same y-top within 12px), mirroring core.ocr.scan_chat_list.
            rows: list[list[dict]] = []
            for t in sorted(
                texts,
                key=lambda t: (min(p[1] for p in t["bbox"]), min(p[0] for p in t["bbox"])),
            ):
                text = (t.get("text") or "").strip()
                if not text:
                    continue
                # Pure-symbol UI artefacts (dividers, dot leaders, etc.) are
                # never the chat name; skip regardless of score.
                if _is_noise_symbol(text):
                    continue
                try:
                    score = float(t.get("score", 0) or 0)
                except (TypeError, ValueError):
                    score = 0.0
                # Title-region threshold: keep short single-character names
                # whose natural confidence sits in [0.35, 0.50). Engine's
                # internal text_score=0.35 already removes true noise below
                # this floor.
                if score < 0.30:
                    continue
                y_top = min(p[1] for p in t["bbox"])
                if rows and abs(y_top - rows[-1][0]["y_top"]) <= 12:
                    rows[-1].append(t)
                else:
                    t["y_top"] = y_top
                    rows.append([t])

            if not rows:
                return ""

            # Top-most row = the chat title; merge its boxes left→right.
            top = sorted(rows[0], key=lambda t: min(p[0] for p in t["bbox"]))
            merged = _merge_name_blocks([(t.get("text") or "").strip() for t in top])

            # 二次识别兜底: 首次结果过短(疑似首字被裁/渲染偏移)时, 左缘再扩
            # 8px 并 2x 放大重 OCR, 取更长的合并结果(截断只会更短)。
            if len(merged) < 6:
                try:
                    from PIL import Image as _Image
                    crop2 = shot.crop((328, 8, 820, 90))
                    crop2 = crop2.resize((crop2.width * 2, crop2.height * 2), _Image.LANCZOS)
                    rows2: list[list[dict]] = []
                    for t in sorted(
                        ocr_recognize(crop2),
                        key=lambda t: (min(p[1] for p in t["bbox"]), min(p[0] for p in t["bbox"])),
                    ):
                        text = (t.get("text") or "").strip()
                        if not text or _is_noise_symbol(text):
                            continue
                        try:
                            if float(t.get("score", 0) or 0) < 0.30:
                                continue
                        except (TypeError, ValueError):
                            pass
                        y_top = min(p[1] for p in t["bbox"])
                        if rows2 and abs(y_top - rows2[-1][0]["y_top"]) <= 24:
                            rows2[-1].append(t)
                        else:
                            t["y_top"] = y_top
                            rows2.append([t])
                    if rows2:
                        top2 = sorted(rows2[0], key=lambda t: min(p[0] for p in t["bbox"]))
                        merged2 = _merge_name_blocks([(t.get("text") or "").strip() for t in top2]).strip()
                        if len(merged2) > len(merged):
                            logger.info("标题二次识别(左扩+2x)取更优: %r -> %r", merged, merged2)
                            merged = merged2
                except Exception:
                    logger.debug("标题二次识别失败", exc_info=True)
            return merged.strip()
        except Exception:
            logger.exception("读取聊天标题失败")
        return ""

    def _title_matches(self, title: str, target: str) -> bool:
        """判断聊天区标题是否可以安全认定为 target 会话。

        口径与 :mod:`target_guard` 完全一致（禁止双向子串、短名不启用相似度、
        相似度阈值 0.85）。旧的 0.6 阈值会让「客户群」误判为「客户群2」
        （ratio=0.857）而放行。
        """
        return name_matches(
            title,
            target,
            allow_truncation=getattr(self._config, "allow_title_truncation", True),
            reject_numbered=getattr(self._config, "reject_numbered_groups", True),
        )

    # ------------------------------------------------------------------
    # 发送前目标守卫（pre-send target guard）
    # ------------------------------------------------------------------

    def set_pending_target(self, name: str) -> None:
        """记录本次发送的目标会话（由 search_contact 成功后自动调用）。"""
        self._pending_target = (name or "").strip() or None
        self._pending_target_since = time.time()

    def clear_pending_target(self) -> None:
        """发送结束（成功或失败）后清除目标锁，避免残留影响下一次发送。"""
        self._pending_target = None
        self._pending_target_since = 0.0

    def _assert_target(self, target: Optional[str] = None, *, retries: int = 3) -> bool:
        """按 Enter 之前的最后一道闸：确认"当前聊天区就是目标会话"。

        为什么必须在这一步复查：search_contact() 通过之后到真正按 Enter 之间
        还有 3~6 秒（sync_delay → 点输入框 → think_pause → 逐字输入/粘贴）。
        这段时间里用户手动点一下微信、后台同步重排会话列表、上一条操作的
        异步渲染延迟生效 —— 任何一个都会让焦点落在别的会话上，而旧代码在这
        段空窗里**没有任何校验**，直接 Enter，消息就发给了错误的人。

        Returns:
            True  = 确认是目标会话，可以发送。
            False = 不是目标 / 无法确认 → **调用方必须放弃发送**。
        """
        if not getattr(self._config, "enable_target_guard", True):
            logger.warning("发送前守卫已在配置中关闭（enable_target_guard=false），本次发送不做目标校验")
            return True

        name = (target or self._pending_target or "").strip()
        if not name:
            # 没有任何目标上下文（例如直接调用 send_text 而没先 search_contact）。
            # 没有基准就无法守卫，只能放行，但必须留下显眼告警。
            logger.warning(
                "发送前守卫未启用：没有目标会话上下文（未调用 search_contact 或未传 target），"
                "无法校验当前会话是否正确"
            )
            audit("发送前守卫", "?", 结果="无目标上下文，跳过校验")
            return True

        title = ""
        for attempt in range(retries):
            title = self._chat_title()
            if title:
                break
            if attempt + 1 < retries:
                time.sleep(0.35)

        if not title:
            logger.error(
                "发送前守卫：读不到聊天区标题（OCR 为空），无法确认当前会话是否为「%s」，放弃发送",
                name,
            )
            audit("发送前守卫", name, 结果="标题为空，拒绝发送")
            return False

        if name_matches(title, name):
            if normalize(title) != normalize(name):
                logger.warning(
                    "发送前守卫：当前会话「%s」与目标「%s」非精确相等但已放行（%s），请留意",
                    title, name, match_reason(title, name),
                )
            audit("发送前守卫", name, 结果="通过", 当前会话=title)
            return True

        logger.error(
            "发送前守卫拦截：当前会话是「%s」，目标却是「%s」（%s），已中止发送并清空输入框",
            title, name, match_reason(title, name),
        )
        audit("发送前守卫", name, 结果="拦截，会话不匹配", 当前会话=title)
        self._clear_input_box()
        return False

    def _clear_input_box(self) -> None:
        """清空输入框内容（守卫拦截后必须调用，防止残留文本被下一条带出去）。

        用 Ctrl+A → Delete 而不是按 N 次退格：消息长度不定，退格次数算不准，
        而全选删除是幂等的。

        ⚠️ 绝不按 ESC：Weixin 4.x 里焦点在聊天区时按 Esc 会收起/隐藏会话面板
        （"隐藏到托盘"的同源行为），实测正是群里看到的"发着发着微信自己
        最小化了"。候选面板在输入框被清空（@查询词消失）后会自行关闭，无需 Esc。
        """
        try:
            pos = self.get_input_box_position()
            if pos:
                sx, sy = self._to_screen(pos[0], pos[1])
                self._human.click_at(sx, sy)
                time.sleep(0.2)
            VK_CONTROL, VK_A, VK_DELETE = 0x11, 0x41, 0x2E
            self._human.press_key_with_modifier(VK_CONTROL, VK_A)
            time.sleep(0.15)
            self._human.send_vk(VK_DELETE)
            time.sleep(0.2)
            logger.info("已清空输入框（发送被守卫拦截）")
        except Exception:
            logger.exception("清空输入框失败")

    def _pre_compose_guard(self, target: Optional[str] = None) -> bool:
        """粘贴/输入之前的最后一道闸：重聚焦微信 + 确认目标会话仍打开。

        search_contact() 之后到真正把文字写进输入框之间还有数秒
        （sync_delay → 点输入框 → think_pause → 逐字输入/粘贴）。这段时间里若
        微信丢失前台（用户点别处、会话/显示状态变化、别的窗口弹到前面），
        点输入框的鼠标点击和 Ctrl+V 就会落进**别的窗口/别的聊天**的输入框 ——
        这正是「息屏/会话异常时把消息粘贴到其他聊天窗口」的根因。

        因此在点输入框之前重聚焦微信并复核聊天区标题仍是目标会话；不满足则
        立即放弃，绝不让草稿进入错误的会话。
        """
        name = (target or self._pending_target or "").strip()
        if not self._window.restore_and_focus():
            logger.error("发送前重聚焦失败：微信窗口未能置前，放弃发送")
            audit("发送前重聚焦", name or "?", 结果="微信未能置前")
            return False
        if not name:
            return True
        return self._chat_selected(name, b"")

    def _chat_selected(self, target: str, before_hash: bytes) -> bool:
        """Confirm the target chat is currently selected.

        只认**聊天区标题**（OCR）。绝不用"内容区 hash 变了"来兜底确认 ——
        点错会话、或新消息把内容区顶动时，内容区都会变，用它会**把错误的会话
        误判成已切到目标**，导致消息粘贴/发送进错误群（用户实测的根因）。
        读不到标题 = 无法确认，返回 False（宁可漏发，绝不发错人）。
        """
        title = self._chat_title()
        if not title:
            return False
        return self._title_matches(title, target)

    def _hash_chat_region(self) -> bytes:
        """Compact content-area hash (reliable chat-switch detector)."""
        try:
            shot = self._get_screenshot()
            if not shot:
                return b""
            x1 = self._config.chat_list_x2 + 10
            region = shot.crop((x1, 60, 880, 500))
            return region.resize((20, 30)).tobytes()
        except Exception:
            return b""

    @staticmethod
    def _is_selection_green(r: int, g: int, b: int) -> bool:
        """True for the selected-chat-list row colour #15AC70 (green)."""
        return g > 130 and (g - r) > 60 and (g - b) > 20

    def _item_is_selected(self, shot, item_x: int, item_y: int) -> bool:
        """True if the chat-list item is the *currently selected* row.

        Selected rows have a green (#15AC70) background; unselected are white
        (#EEEEF0); pinned are grey (#E6E6E8).  Used to avoid re-clicking an
        already-open chat (which DESELECTS it and blanks the content area).
        """
        try:
            img = shot.load()
            green = 0
            total = 0
            # sample points in the row background between the avatar and name
            for dx in (-45, -35, -25):
                for dy in (-5, 0, 5):
                    x, y = item_x + dx, item_y + dy
                    if 0 <= x < shot.width and 0 <= y < shot.height:
                        r, g, b = img[x, y][:3]
                        total += 1
                        if self._is_selection_green(r, g, b):
                            green += 1
            return total > 0 and green >= max(1, total // 2)
        except Exception:
            return False

    def _focus_chat_list(self) -> None:
        """Click on chat list to focus it (human-simulated)."""
        if self._chat_list_region:
            x1, y1, x2, y2 = self._chat_list_region
            center_x, center_y = self._to_screen((x1 + x2) // 2, (y1 + y2) // 2)
            self._human.click_at(center_x, center_y)
            time.sleep(0.3)

    def _scroll_to_top(self) -> None:
        """Scroll chat list to top (human-simulated)."""
        if self._chat_list_region:
            x1, y1, x2, y2 = self._chat_list_region
            center_x, center_y = self._to_screen((x1 + x2) // 2, (y1 + y2) // 2)
            self._human.scroll_list(center_x, center_y, direction="up", amount=30)
            time.sleep(0.5)

    def _scroll_down(self, ticks: int = 4) -> bool:
        """Scroll down and return True if new content appeared."""
        if not self._chat_list_region:
            return False
        x1, y1, x2, y2 = self._chat_list_region
        center_x, center_y = self._to_screen((x1 + x2) // 2, (y1 + y2) // 2)

        old_screenshot = self._get_screenshot()

        self._human.scroll_list(center_x, center_y, direction="down", amount=ticks)
        time.sleep(0.8)

        new_screenshot = self._get_screenshot()
        if old_screenshot and new_screenshot:
            old_items = scan_chat_list(old_screenshot, self._chat_list_region)
            new_items = scan_chat_list(new_screenshot, self._chat_list_region)
            old_names = {item.name for item in old_items}
            new_names = {item.name for item in new_items}
            if old_names == new_names:
                logger.debug("No new content after scrolling")
                return False
        return True

    def scan_baseline(self) -> bool:
        if not self._update_window_rect():
            return False
        if not self._chat_list_region:
            self.find_chat_list()

        self._window.bring_to_front()
        time.sleep(0.5)

        logger.info("正在扫描聊天列表基线...")

        self._focus_chat_list()
        self._scroll_to_top()

        all_items: list[ChatListItem] = []
        seen_names: set[str] = set()

        screenshot = self._get_screenshot()
        if screenshot and self._chat_list_region:
            items = scan_chat_list(screenshot, self._chat_list_region)
            for item in items:
                if item.name not in seen_names:
                    seen_names.add(item.name)
                    all_items.append(item)

        no_new_count = 0
        for scroll_round in range(15):
            has_new = self._scroll_down(random.randint(3, 6))

            screenshot = self._get_screenshot()
            if screenshot and self._chat_list_region:
                items = scan_chat_list(screenshot, self._chat_list_region)
                new_count = 0
                for item in items:
                    if item.name not in seen_names:
                        seen_names.add(item.name)
                        all_items.append(item)
                        new_count += 1

                if new_count == 0:
                    no_new_count += 1
                else:
                    no_new_count = 0

                if no_new_count >= 2:
                    logger.info("连续 2 轮无新内容，停止滚动")
                    break

        self._baseline_items = all_items
        self._baseline_scanned = True

        logger.info("基线扫描完成: 找到 %d 个联系人/群", len(all_items))
        for item in all_items[:30]:
            logger.info("  - %s", item.name)
        if len(all_items) > 30:
            logger.info("  ... and %d more", len(all_items) - 30)

        return True

    def search_contact(self, name: str) -> bool:
        if not self._update_window_rect():
            return False

        self._window.bring_to_front()
        time.sleep(0.5)

        logger.info("正在搜索联系人: %s", name)

        self._focus_chat_list()
        self._scroll_to_top()

        no_new_streak = 0
        for scroll_round in range(25):
            screenshot = self._get_screenshot()
            if screenshot and self._chat_list_region:
                items = scan_chat_list(screenshot, self._chat_list_region)
                match = find_contact_by_name(items, name)

                if match:
                    logger.info("找到 '%s'（识别为 '%s'，位置 %d,%d）", name, match.name, match.x_position, match.y_position)

                    # Two-step human-like approach: move near, then fine-aim and click.
                    sx, sy = self._to_screen(match.x_position - 20, match.y_position)
                    self._human.move_mouse_to(sx, sy)
                    time.sleep(0.15)

                    # Click and VERIFY (up to a few passes).  CRITICAL: WeChat
                    # DESELECTS a chat when you click an already-selected item
                    # (the content area goes blank).  So if the item is already
                    # the active (green #15AC70) one, we do NOT click it again —
                    # we just confirm.  Only click when it isn't selected yet.
                    before_hash = self._hash_chat_region()
                    switched = False
                    for attempt in range(4):
                        self._window.bring_to_front()
                        time.sleep(0.12)
                        shot_now = self._get_screenshot()
                        already = self._item_is_selected(shot_now, match.x_position, match.y_position)
                        if already:
                            # already the active chat -> never re-click (would deselect)
                            if self._chat_selected(name, before_hash):
                                switched = True
                                break
                            # title OCR lagging / blank: wait, don't click again
                            time.sleep(0.5)
                            continue
                        # not selected -> click once to open it
                        sx, sy = self._to_screen(match.x_position, match.y_position)
                        self._human.click_at(sx, sy)
                        time.sleep(1.0)
                        if self._chat_selected(name, before_hash):
                            switched = True
                            break
                        title = self._chat_title()
                        logger.info(
                            "会话未选中（当前标题: %s），准备下一步", title or "?（内容区未变）",
                        )
                        time.sleep(0.4)

                    if not switched:
                        logger.error("多次点击仍未切换到 '%s'，放弃发送（避免发错人）", name)
                        audit("搜索联系人", name, 结果="切换失败", 滚动轮数=scroll_round + 1, 重试=4)
                        self.clear_pending_target()
                        return False

                    # 锁定目标：后续 send_* 会在按 Enter 前复查聊天区标题
                    self.set_pending_target(name)
                    audit("搜索联系人", name, 结果="找到", 滚动轮数=scroll_round + 1, 重试=attempt + 1)
                    return True

            has_new = self._scroll_down(random.randint(3, 5))
            if has_new:
                no_new_streak = 0
            else:
                no_new_streak += 1
                if no_new_streak >= 2:
                    # ADR-0008: 主列表扫描对「折叠置顶/深层历史会话」不可靠
                    # (现场: 目标群在折叠置顶区, 主列表怎么滚都没有)。确定性
                    # 定位 = 微信搜索框: 点搜索框 → 输入全名 → 唯一结果点击。
                    if self._search_via_box(name):
                        audit("搜索联系人", name, 结果="搜索框命中", 滚动轮数=scroll_round + 1)
                        self.set_pending_target(name)
                        return True
                    # 次选: 展开折叠置顶聊天后重扫一次; 展开按钮不存在（无置顶
                    # 折叠）则按原逻辑快速失败并告警。
                    if self._expand_folded_top_chats():
                        logger.info("已展开折叠置顶聊天, 重扫会话列表找 '%s'", name)
                        no_new_streak = 0
                        self._scroll_to_top()
                        continue
                    logger.error("已滚动到聊天列表底部仍未找到 '%s'（连续 2 轮无新内容；"
                                 "若为锁屏/输入失效请先恢复会话桌面）", name)
                    audit("搜索联系人", name, 结果="未找到(到底,疑似输入失效)", 滚动轮数=scroll_round + 1)
                    self.clear_pending_target()
                    return False

            if scroll_round % 5 == 0:
                logger.info("滚动第 %d 轮...", scroll_round + 1)

        logger.error("滚动后仍未找到联系人 '%s'", name)
        audit("搜索联系人", name, 结果="未找到", 滚动轮数=25)
        self.clear_pending_target()
        return False

    def _search_via_box(self, name: str) -> bool:
        """通过微信搜索框确定性定位会话（ADR-0008）。

        主列表扫描对折叠置顶/深层历史天然不可靠; 搜索框输入全名后, 结果列表
        显示**完整群名**, target_guard 严格口径即可安全匹配。
        流程: OCR 定位「搜索」框 → 点击 → 逐字输入全名 → 等 → OCR 结果列表
        → find_contact_by_name(严格) → 唯一命中点击 → 标题复核。
        """
        try:
            r = self._window.get_window_rect()
            if not r:
                return False
            from . import capture
            img = capture.grab_screen_region(r[0], r[1], r[2], r[3])
            if not img:
                return False
            box = None
            for t in ocr_recognize(img):
                txt = t.get("text") or ""
                if ("搜索" in txt or "搜素" in txt or "捜索" in txt) and len(txt) <= 8:
                    xs = [p[0] for p in t["bbox"]]
                    ys = [p[1] for p in t["bbox"]]
                    # 点击搜索框文本右侧的输入区
                    box = (r[0] + int(max(xs)) + 18, r[1] + int(sum(ys) / len(ys)))
                    break
            if not box:
                logger.info("搜索框未定位到（OCR 无「搜索」文本）")
                return False
            logger.info("搜索框路径: 点击(%d,%d) 输入 %r", box[0], box[1], name)
            self._human.click_at(*box)
            time.sleep(0.6)
            self._human.type_text_natural(name)
            time.sleep(1.4)

            # OCR 搜索结果（主列表区域即结果列表区域）
            img2 = capture.grab_screen_region(r[0], r[1], r[2], r[3])
            if not img2:
                return False
            region = img2.crop((0, 40, self._config.chat_list_x2 + 20, img2.height))
            items = scan_chat_list(region, (0, 40, self._config.chat_list_x2 + 20, img2.height))
            match = find_contact_by_name(items, name) if items else None
            if not match:
                logger.info("搜索框结果中没有 %r", name)
                # 清搜索: 连按 Esc 返回主界面
                user32.keybd_event(0x1B, 0, 0, 0)   # ESC down: 退出搜索界面
                user32.keybd_event(0x1B, 0, 2, 0)   # ESC up
                time.sleep(0.5)
                return False
            logger.info("搜索框命中 '%s'（识别为 '%s'，%d,%d）", name, match.name,
                        match.x_position, match.y_position)
            sx, sy = self._to_screen(match.x_position, match.y_position)
            self._human.click_at(sx, sy)
            time.sleep(1.2)
            if self._chat_selected(name, b""):
                logger.info("搜索框路径成功切换到 %r", name)
                return True
            logger.warning("搜索框点击后标题未确认(%r)", self._chat_title())
            return False
        except Exception:
            logger.exception("搜索框路径异常")
            return False

    def _expand_folded_top_chats(self) -> bool:
        """OCR 定位「折叠置顶聊天」按钮并点击展开（ADR-0007）。

        目标群被置顶且微信折叠了置顶区时, 主会话列表看不到它; 用户经验:
        点击「折叠置顶聊天」展开后即可见。返回是否找到并点击了按钮。
        """
        try:
            r = self._window.get_window_rect()
            if not r:
                return False
            img = capture.grab_screen_region(r[0], r[1], r[2], r[3])
            if not img:
                return False
            for t in ocr_recognize(img):
                if "折叠置顶聊天" in (t.get("text") or ""):
                    xs = [p[0] for p in t["bbox"]]
                    ys = [p[1] for p in t["bbox"]]
                    cx = r[0] + int(sum(xs) / len(xs))
                    cy = r[1] + int(sum(ys) / len(ys))
                    logger.info("发现折叠置顶聊天按钮(%d,%d), 点击展开", cx, cy)
                    self._human.click_at(cx, cy)
                    time.sleep(1.2)
                    return True
        except Exception:
            logger.exception("展开折叠置顶聊天异常")
        return False

    def get_input_box_position(self) -> Optional[tuple[int, int]]:
        return (self._config.input_box_x, self._config.input_box_y)

    def check_version(self) -> Optional[str]:
        """Read the installed WeChat exe version (best-effort, wxauto-style).

        Returns the version string or None when it cannot be read.
        """
        try:
            hwnd = self._window.find_window()
            if not hwnd:
                return None
            import ctypes.wintypes as wintypes
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            path = self._get_process_path(pid.value)
            if not path:
                return None
            ver = _get_file_version(path)
            if ver:
                cfg_ver = self._config.adapter_version
                # config may be a prefix (e.g. "4.1.13" vs exe "4.1.13.12")
                if cfg_ver and not ver.startswith(cfg_ver):
                    logger.warning(
                        "微信版本 %s 与适配器配置版本 %s 不一致，界面元素位置可能偏移！",
                        ver, cfg_ver,
                    )
            return ver
        except Exception:
            logger.exception("读取微信版本失败")
            return None

    @staticmethod
    def _get_process_path(pid: int) -> Optional[str]:
        try:
            import ctypes.wintypes as wintypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return None
            try:
                buf = ctypes.create_unicode_buffer(512)
                size = ctypes.wintypes.DWORD(512)
                ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
                return buf.value if ok else None
            finally:
                ctypes.windll.kernel32.CloseHandle(h)
        except Exception:
            return None

    def get_send_button_position(self) -> Optional[tuple[int, int]]:
        return (self._config.send_button_x, self._config.send_button_y)

    def _send_message(self) -> bool:
        """按 Enter 发送，并事后验证"确实发出去了"。

        旧实现只看"发送按钮变灰"：但**输入框为空时按钮本来就是灰的** ——
        焦点丢失/按键没进微信时，Enter 什么都没做，按钮依旧灰 → 被误判
        "已发送"（假阳性，IMAGE=sent 实为没发出去就是这么来的）。

        现在双条件缺一不可：
        1. Enter 后按钮不为绿色（绿色=输入框还有内容没发走，确定未发送）；
        2. Enter 后输入框内容**消失**（发送成功的标志是文本/图片离开输入框）。
        两者都满足 → True；任一不满足 → False；无法判定 → fail-open True
        （刻意：Enter 确实按下去了，误报失败导致上游重发的代价更高）。
        """
        before = self._input_box_signature()
        self._human._send_enter()
        time.sleep(0.8)

        if not getattr(self._config, "verify_send_result", True):
            audit("发送", "Enter键", 结果="已发送（未启用结果验证）")
            return True

        state = self._send_button_active()
        after = self._input_box_signature()

        def _dark(sig: bytes) -> int:
            return sum(1 for v in sig if v < 140)

        # 发送成功的特征：框内文字/图片离开 → 暗像素（文字）大幅减少。
        # 按 Enter 没被微信接收时，框内容与按之前逐像素一致（截图同源，
        # 无抖动）→ 判"未发送"。before 取不到时不做此判据。
        box_still_full = (before is not None and after is not None
                          and _dark(after) > max(8, 0.5 * _dark(before)))

        if state is True:
            logger.error("按 Enter 后发送按钮仍为绿色：消息未发出（焦点可能已丢失）")
            audit("发送", "Enter键", 结果="未发送（按钮仍绿）")
            return False
        if state is None and (before is None or after is None):
            logger.warning("无法判定发送结果（截图/取色失败），按已发送处理")
            audit("发送", "Enter键", 结果="无法判定，按已发送")
            return True
        if box_still_full:
            logger.error("按 Enter 后输入框内容未消失：消息没有真正发出（按键未被微信接收？）")
            audit("发送", "Enter键", 结果="未发送（输入框内容仍在）")
            return False
        audit("发送", "Enter键", 结果="已发送（按钮变灰且输入框已清空）")
        return True

    def _send_button_active(self) -> Optional[bool]:
        """发送按钮是否还是可点击的绿色 '#00C375'（即消息还没发出去）。

        Returns:
            True  = 仍是绿色，**确认未发送**
            False = 已变灰，**确认已发送**
            None  = 无法判定（截图失败 / 坐标缺失），调用方须自行兜底

        旧签名只返回 bool，无法区分"确认没发"和"看不清" —— 后者会让验证
        逻辑把一次正常发送误判成失败，上游一重试就变成重复发消息。
        """
        try:
            shot = self._get_screenshot()
            pos = self.get_send_button_position()
            if not shot or not pos:
                return None  # inconclusive
            x, y = pos[0], pos[1]
            region = shot.crop((x - 40, y - 32, x + 45, y + 12))
            px = region.load()
            green = 0
            total = region.width * region.height
            for yy in range(region.height):
                for xx in range(region.width):
                    r, g, b = px[xx, yy][:3]
                    # greenish #00C375 (tolerant of shading/gradients)
                    if g > 150 and r < 90 and b < 180 and (g - r) > 60:
                        green += 1
            return green > 0.03 * total
        except Exception:
            logger.exception("检测发送按钮颜色失败")
            return None

    def _click_send_button(self, max_retries: int = 3) -> bool:
        """Click the send button and verify it turned grey (message sent).

        If the click missed (still green) due to randomized target / tremor,
        re-aim and click again until grey or retries exhausted.
        """
        send_pos = self.get_send_button_position()
        if not send_pos:
            return True
        for attempt in range(max_retries):
            sx, sy = self._to_screen(send_pos[0], send_pos[1])
            self._human.click_at(sx, sy)
            time.sleep(0.6)
            state = self._send_button_active()
            if state is False:
                audit("发送按钮", f"({send_pos[0]},{send_pos[1]})", 结果="变灰已发送", 尝试=attempt + 1)
                return True
            if state is None:
                # 看不清就不再盲点：再点一次可能发出第二条
                logger.warning("发送按钮状态无法判定，停止点击以避免重复发送")
                audit("发送按钮", f"({send_pos[0]},{send_pos[1]})", 结果="无法判定", 尝试=attempt + 1)
                return True
            logger.info("发送按钮仍为绿色（未发送），第 %d 次纠错重按", attempt + 2)
            time.sleep(0.4)
        logger.error("发送按钮重试 %d 次后仍未变灰，可能发送失败", max_retries)
        audit("发送按钮", f"({send_pos[0]},{send_pos[1]})", 结果="仍绿色", 尝试=max_retries)
        return False

    def get_chat_list_items(self) -> list[dict[str, str]]:
        # Lazy baseline: walk the chat list only when actually requested
        # (startup no longer scans the whole contact list).
        if not getattr(self, "_baseline_scanned", False):
            try:
                self.scan_baseline()
            except Exception:
                logger.exception("懒扫描聊天列表基线失败")
                return []
        return [{"name": item.name, "text": item.name} for item in self._baseline_items[:30]]

    def is_in_chat_view(self) -> bool:
        return self.get_input_box_position() is not None

    def _input_box_signature(self) -> Optional[bytes]:
        """输入框文本区域的低分辨率灰度指纹（用于输入前后做差分）。

        缩到 64×8 再取灰度：既保留"有没有字"的信息，又把抗锯齿/光标闪烁
        带来的单像素噪声抹掉。

        ⚠️ crop 必须覆盖**整个文本区**：Weixin 4.x 输入框是三行大文本区，
        文字从顶部往下排、底部锚定（get_input_box_position 的 y=515 只是
        靠下的一行）。旧 crop 以该点为中心只盖住文本区下缘+工具栏上沿 ——
        第一行短文本（@、单词）根本不经过这片像素，差分恒为 0，把"发送
        成功"误判成"输入框验证失败"，进而引发按步重试重复刷屏。
        """
        try:
            shot = self._get_screenshot()
            pos = self.get_input_box_position()
            if not shot or not pos:
                return None
            x, y = pos[0], pos[1]
            # 从输入框最左缘到最右缘，纵向覆盖整个文本区（点击行往上 3 行 + 往下 1 行），
            # 避开工具栏图标（表情/文件在 y+40 以下）。
            x1 = max(0, x - 250)
            x2 = min(shot.width, x + 300)
            y1 = max(0, y - 80)
            y2 = min(shot.height, y + 25)
            region = shot.crop((x1, y1, x2, y2)).convert("L")
            return region.resize((64, 8)).tobytes()
        except Exception:
            return None

    def _input_box_changed(self, before: Optional[bytes]) -> bool:
        """输入框相对于 `before` 指纹是否发生了变化（即内容是否真的写进去了）。

        `before` 为 None（取不到指纹）时无法判定，按"已写入"处理，避免
        把一次正常输入误判成失败进而重试（重试会输入第二遍）。
        """
        if before is None:
            return True
        after = self._input_box_signature()
        if after is None:
            return True
        if len(after) != len(before):
            return True
        diff = sum(1 for a, b in zip(after, before) if abs(a - b) > 18)
        return diff >= 3

    @staticmethod
    def _type_char_by_char(text: str) -> bool:
        """这段文本是否适合逐字键入（IME 拟人节奏）。

        多行或含 emoji（增补平面字符）的文本一律走**剪贴板粘贴**：粘贴能 1:1
        保真换行与 emoji；逐字路径虽然也能处理（Ctrl+Enter 换行 + UTF-16 代理
        对），但组合键节奏易被个别输入框/输入法状态吞字符，重要文案不值得赌。
        纯单行 BMP 短文本仍逐字键入，保留拟人节奏。
        """
        if "\n" in text or "\r" in text:
            return False
        if any(ord(c) > 0xFFFF for c in text):
            return False
        return len(text) <= 50

    def _ensure_input_content(self, text: str, max_retries: int = 3,
                              force_paste: bool = False) -> bool:
        """Ensure `text` landed in the input box (paste/type with verification).

        When the clipboard API fails (clipboard locked by another process),
        degrade to char-by-char typing so the message is still delivered.

        校验方式从"数暗像素"改为**输入前后截图差分**：前者在 580×40 的区域里
        恒为真（边框/光标/工具栏图标本身就贡献上百个暗像素），等于没校验；
        差分只在真的有像素变化时才认为写入成功。

        重试前会先清空输入框，否则第二次输入会追加在第一次后面，发出去的是
        两份内容 —— 这也是"重复发送"的一个隐蔽来源。
        """
        before: Optional[bytes] = None
        for attempt in range(max_retries):
            if attempt == 0:
                before = self._input_box_signature()
            else:
                # 上一轮可能写入了半截内容，先清干净再重来
                self._clear_input_box()
                before = self._input_box_signature()

            if (not force_paste) and self._type_char_by_char(text):
                self._human.type_text_natural(text)
            else:
                # force_paste：逐字键入会逐字符触发候选面板/@提醒弹窗等
                # 交互状态，粘贴则整段落地不触发 —— 文字直发必须走粘贴。
                if not self._clipboard.paste_text(text):
                    logger.info("剪贴板写入失败，降级逐字输入（第 %d 次尝试）", attempt + 1)
                    self._human.type_text_natural(text)
            time.sleep(0.6)

            if self._input_box_changed(before):
                return True
            logger.info("输入框内容无变化（第 %d 次重试输入）", attempt + 2)
            time.sleep(0.4)
        logger.error("输入框内容验证失败 %d 次", max_retries)
        return False

    def send_text(self, text: str, target: Optional[str] = None) -> bool:
        try:
            pos = self.get_input_box_position()
            if not pos:
                logger.error("未找到输入框位置")
                return False

            # 点输入框/粘贴前先重聚焦微信并确认目标会话仍打开（防「息屏/会话
            # 异常时粘贴进别的聊天窗口」）。search_contact 到真正输入之间还有数秒，
            # 微信若在这段时间丢失前台，鼠标点击和 Ctrl+V 会落进别的窗口/聊天。
            if not self._pre_compose_guard(target):
                self._clear_input_box()
                self.clear_pending_target()
                return False

            # WeChat: after opening a chat the input box is focused, BUT any click
            # in the chat content steals that focus.  So re-focus the input box
            # right before typing (clicking it is harmless & reliable).
            sx, sy = self._to_screen(pos[0], pos[1])
            self._human.click_at(sx, sy)
            time.sleep(0.3)
            self._human.think_pause()  # thinking pause before composing
            if not self._ensure_input_content(text):
                audit("发送文本", f"({pos[0]},{pos[1]})", 结果="输入框内容验证失败")
                return False
            time.sleep(1)

            # 按 Enter 之前的最后一道闸：确认当前会话仍然是目标会话
            if not self._assert_target(target):
                audit("发送文本", f"({pos[0]},{pos[1]})", 结果="目标守卫拦截", 目标=target or self._pending_target)
                self.clear_pending_target()
                return False

            ok = self._send_message()
            if not ok and self._own_bubble_sent(text.split("\n")[0][:8]):
                logger.info("文本发送复核：按钮/清空判定失败，但聊天区底部已出现含该文字的绿色"
                            "本人气泡 → 判定已发送（防误判重发）")
                ok = True

            time.sleep(1)
            if ok:
                logger.info("文本已发送: %s", text[:50])
                audit("发送文本", f"({pos[0]},{pos[1]})",
                      方式="逐字" if self._type_char_by_char(text) else "粘贴", 长度=len(text))
            else:
                audit("发送文本", f"({pos[0]},{pos[1]})", 结果="发送未确认")
                self._clear_input_box()   # 清残留，防止下一条把半截文字拼上一起发
            self.clear_pending_target()
            return ok
        except Exception:
            logger.exception("Failed to send text")
            self.clear_pending_target()
            return False

    # 微信"自己发出"消息的气泡底色 #9DF29F（别人的是灰 #EEEEF0、在左侧；
    # 本人的在右侧绿色）——用它区分"刚才是不是我们发出去的"
    _BUBBLE_GREEN_RGB = (157, 242, 159)

    def _own_bubble_sent(self, keyword: Optional[str] = None) -> bool:
        """聊天区**底部**是否有我们刚发出的绿色气泡（可选要求气泡文字含 keyword）。

        自己发的消息 = 右侧、绿底 #9DF29F；别人发的 = 左侧、灰底 #EEEEF0。
        只扫聊天区右半，且绿色行必须贴到聊天区最底部（= 最新一条），旧的绿色
        气泡不作数。作为 `_send_message`（按钮变灰 + 输入框清空）判 False 后的
        复核兜底：消息其实已发出却被判失败 → 上游重试 → 群里刷屏，就是这么来
        的。截图/取色失败返回 False，不改变原判定。
        """
        try:
            shot = self._get_screenshot()
            pos = self.get_input_box_position()
            if not shot or not pos:
                return False
            x, y = pos[0], pos[1]
            x1, y1, x2, y2 = max(0, x - 280), max(0, y - 330), min(shot.width, x + 310), max(0, y - 85)
            if x2 - x1 < 100 or y2 - y1 < 40:
                return False
            gr, gg, gb = self._BUBBLE_GREEN_RGB
            px = shot.load()
            green_rows = []
            for yy in range(y1, y2, 2):                              # 行步进 2px
                cnt = 0
                for xx in range(x1 + (x2 - x1) // 2 + 10, x2, 3):    # 右半侧, 列步进 3px
                    r, g, b = px[xx, yy][:3]
                    if abs(g - gg) < 35 and abs(r - gr) < 45 and abs(b - gb) < 45:
                        cnt += 1
                if cnt >= 6:
                    green_rows.append(yy)
            if not green_rows:
                return False
            if green_rows[-1] < y2 - 26:      # 绿色必须在聊天区最底部（最新消息位）
                return False
            if keyword is None:
                return True
            band_top = max(y1, min(green_rows) - 6)
            strip = shot.crop((x1 + (x2 - x1) // 3, band_top, x2, min(shot.height, green_rows[-1] + 8)))
            joined = "".join(t.get("text", "") for t in ocr_recognize(strip)).replace(" ", "")
            return keyword.replace(" ", "") in joined
        except Exception:
            logger.debug("_own_bubble_sent 检测异常", exc_info=True)
            return False

    def send_at(self, text: str, at_name: str, target: Optional[str] = None) -> bool:
        """Send a text message with an @mention (group chat), covertly.

        v5.0.1 @ 策略（ADR-0008, 用户确认）:
        1) "picked" —— 候选面板唯一命中 → Enter 选中 chip（真 @，带提及提醒），
           补正文后发送；
        2) "ambiguous" —— 候选不唯一（长名打前缀时全部截断显示
           "安信德&创客龙...", 无法认定哪条是谁）→ **清空面板输入，直接粘贴
           "@全称 正文" 发送**。此时微信不产生真@提醒，但消息内容明确记录了
           @ 谁；万一认错人也是用户配置的成员名不精确，责任口径清晰；
        3) "none" —— 面板没弹/打全名也无候选 → 复核绿色气泡（可能已直发），
           否则同样粘贴 "@全称 正文" 发送，绝不丢提醒；
        成功判据统一 = 聊天区底部出现含该名字的绿色本人气泡。
        """
        try:
            pos = self.get_input_box_position()
            if not pos:
                logger.error("未找到输入框位置")
                return False

            # 点输入框/输入前先重聚焦微信并确认目标会话仍打开（防粘贴进别的聊天窗口）。
            if not self._pre_compose_guard(target):
                self._clear_input_box()
                self.clear_pending_target()
                return False

            sx, sy = self._to_screen(pos[0], pos[1])
            self._human.click_at(sx, sy)
            time.sleep(0.3)
            self._human.think_pause()  # thinking pause before composing

            body = (text or "").strip()
            literal = f"@{at_name}" + (f" {body}" if body else "")

            # 1) try real mention via the candidate panel
            picked = self._type_at_and_pick(at_name) in ("picked",)

            if picked:
                # 2a) chip 已选中：补正文
                if body and not self._ensure_input_content(body):
                    audit("发送@消息", f"@{at_name}", 结果="输入框内容验证失败")
                    self._clear_input_box()
                    self.clear_pending_target()
                    return False
            else:
                # 2b) picked 失败（含 ambiguous 多候选 / none 无候选）:
                # 先复核"是不是已经发出去了"——pick 过程中对唯一候选按过的
                # Enter 可能已把"@名字"当文字发出（旧绿色气泡会被当成候选行,
                # 佛山/广州群实测发生过）。已发出就直接成功, 绝不补发第二条。
                if self._own_bubble_sent(at_name):
                    logger.info("@复核：候选未选中但聊天区底部已有含「%s」的绿色本人气泡，"
                                "按已发送处理（不补发）", at_name)
                    audit("发送@消息", f"@{at_name}", 结果="文字已发出（绿色气泡复核）")
                    self.clear_pending_target()
                    return True
                # 粘贴 "@全称 正文" 直发（ADR-0008: ambiguous/none 共用此降级）
                self._clear_input_box()
                if not self._ensure_input_content(literal, force_paste=True):
                    audit("发送@消息", f"@{at_name}", 结果="文字直发未能写入输入框，放弃发送")
                    self._clear_input_box()
                    self.clear_pending_target()
                    return False
                if not self._input_box_has_name(at_name):
                    audit("发送@消息", f"@{at_name}", 结果="文字直发缺@名字，放弃发送")
                    self._clear_input_box()
                    self.clear_pending_target()
                    return False
            time.sleep(1)

            # 按 Enter 之前的最后一道闸（@ 消息比普通文本多了一段候选面板
            # 交互，中间被打断切走会话的概率更高，守卫更不可少）
            if not self._assert_target(target):
                audit("发送@消息", f"@{at_name}", 结果="目标守卫拦截", 目标=target or self._pending_target)
                self.clear_pending_target()
                return False

            ok = self._send_message()
            if not ok and self._own_bubble_sent(at_name):
                logger.info("@消息复核：输入框判定未通过，但聊天区底部已出现含「%s」的绿色"
                            "本人气泡 → 判定已发送（防误判重发）", at_name)
                ok = True

            time.sleep(1)
            if ok:
                logger.info("@ 消息已发送: @%s %s (弹窗选择=%s)", at_name, text[:50], picked)
                audit("发送@消息", f"@{at_name}", 长度=len(text), 方式="真@选人" if picked else "文字直发")
            else:
                audit("发送@消息", f"@{at_name}", 结果="发送未确认")
                self._clear_input_box()   # 清残留，防止下一条把半截文字拼上一起发
            self.clear_pending_target()
            return ok
        except Exception:
            logger.exception("发送 @ 消息失败")
            self.clear_pending_target()
            return False

    def _type_at_and_pick(self, at_name: str) -> str:
        """Type '@' and pick the member from the @ candidate panel.

        Returns "picked" | "ambiguous" | "none"（ADR-0008）。

        Phase A: type from the START (prefix); WeChat shows same-prefix members
        and we re-OCR until the target is uniquely matched, then press ENTER to
        select it (Enter confirms the top suggestion -> a real mention).

        选不中人宁可放弃：绝不"盲按 Enter 接受顶部建议"——面板没开时那一下
        Enter 会把输入框里未选中的"@xxx"文本原样发出去（杨露重复发送事故根因）。
        """
        state = self._try_search_and_pick(at_name, at_name)
        if state == "none":
            logger.info("@成员未能从候选面板选中，放弃@发送（不盲按Enter）: %s", at_name)
        return state

    def _type_at_suffix(self, at_name: str) -> bool:
        """Type '@' + the FULL name, then try to pick from the panel.

        v0.4.17：不再"先打后缀再打全名"两段式。任何一步中途的 Enter 都可能
        把输入框里已打的文字当消息发出 —— 只打全名保证：即便 Enter 落空成
        直发，发出的也是完整的"@全名"（正是用户要的那条），不会残缺/多余。
        """
        return self._try_search_and_pick(at_name, at_name)

    def _try_search_and_pick(self, at_name: str, query: str) -> str:
        """Type '@'+query SLOWLY (human), OCR the panel and Enter if the target
        is strongly shown as the top suggestion.

        Returns: "picked" | "ambiguous" | "none"（ADR-0008 三态）。
        ambiguous(多候选截断名)不再徒劳重试 —— 直接交 send_at 走粘贴全称。"""
        # 打半角 '@'(U+0040, 直接注入字符)：Shift+2 在中文输入法下可能打出
        # 全角"＠"，微信候选面板对全角**完全不弹** —— 之前日志里候选行全是
        # 聊天气泡、面板从未出现过的根因就在这里。
        self._human._send_unicode_char(0x40)
        time.sleep(random.uniform(0.15, 0.25))
        audit("输入", "@", 方式="Unicode注入")
        self._type_at_query(query)
        time.sleep(0.9)
        for _ in range(3):
            ok, _order, _sim, ambiguous = self._try_pick_at_member(at_name)
            if ok:
                return "picked"
            if ambiguous:
                return "ambiguous"
            time.sleep(0.4)
        return "none"

    def _type_at_query(self, query: str) -> None:
        """Type the @ query one char at a time with human pauses (never instant)."""
        for i, ch in enumerate(query):
            self._human.type_text_natural(ch)
            if i < len(query) - 1:
                time.sleep(random.uniform(0.12, 0.3))
        time.sleep(random.uniform(0.3, 0.6))

    def _try_pick_at_member(self, at_name: str):
        """OCR the @ candidate panel and count the candidate rows.

        WeChat shows a candidate's TRUNCATED name (e.g. "安信德＆创客龙..."),
        so a full-name similarity score is unreliable.  Instead: if exactly ONE
        candidate row is shown, the query uniquely matched -> press ENTER to
        select it.  If 0 or >1 rows, return not-selected (caller types more).
        Returns (selected, row_count, best_similarity, ambiguous).

        v5.0.1(ADR-0008): ambiguous = 面板行数 > 1 —— 长名字打前缀时候选全部
        截断显示("安信德&创客龙..."), 无法唯一认定, 调用方(send_at)按用户策略
        直接粘贴"@全称 正文"发送。
        """
        try:
            shot = self._get_screenshot()
            pos = self.get_input_box_position()
            if not shot or not pos:
                return False, 0, 0.0, False
            x, y = pos[0], pos[1]
            left = int(self._config.chat_list_x2) + 8
            # 下缘止于 y-95（聊天区/面板带）：旧值 y-30 把**输入框首行**也扫进
            # 候选（刚打的"@杨露"自己变成一行候选 → 永远选不中/干扰判断）。
            r = (left, max(0, y - 340), 885, max(0, y - 95))
            region = shot.crop(r)
            texts = ocr_recognize(region)

            nm = _norm_id(at_name)

            def _name_related(line: str) -> bool:
                """候选行与目标成员名相关（面板行是被 query 过滤过的成员名，
                可能带截断省略号；聊天气泡/号码/时间行基本不会命中）。
                v5.0.1: 归一化比较(下划线/空格/大小写差异抹掉)。"""
                cand = _norm_id(line).strip(".")
                if not cand or len(cand) < 2:
                    return False
                return cand in nm or nm[:2] in cand or nm.endswith(cand[-2:])

            # candidate rows = short name-like blocks (skip times / placeholders)
            rows = []
            for t in texts:
                line = (t.get("text") or "").strip()
                if not (2 <= len(line) <= 32):
                    continue
                # 含 @ 的行**不是**候选面板行：面板里只有成员名。之前正是把
                # 聊天区自己发过的旧"@杨露"绿色气泡当成了唯一候选,才引发盲按
                # Enter 的连环事故。
                if "@" in line or "＠" in line:
                    continue
                if re.match(r"^\d{1,2}[:：]\d{2}$", line):
                    continue
                if "条新消息" in line or "撤回" in line:
                    continue
                if not _name_related(line):
                    continue
                rows.append({"line": line, "bbox": t["bbox"], "y": min(p[1] for p in t["bbox"])})
            rows.sort(key=lambda c: c["y"])
            if not rows:
                return False, 0, 0.0, False
            logger.info("@候选行数: %d -> %s", len(rows), [r2["line"] for r2 in rows])

            best_sim = 0.0
            best_line = rows[0]["line"]
            try:
                best_sim = difflib.SequenceMatcher(None, at_name.replace(" ", ""), best_line.replace(" ", "")).ratio()
            except Exception:
                best_sim = 0.0

            if len(rows) == 1:
                # the query uniquely matched a member -> ENTER selects it
                self._human._send_enter()
                time.sleep(0.7)
                if self._input_box_has_name(at_name):
                    logger.info("唯一候选，已用 Enter 选择@成员: %s (显示 %s)", at_name, best_line)
                    return True, 1, best_sim, False
            # 多候选(>1): 截断名无法唯一认定 → 交由 send_at 粘贴全称策略
            return False, len(rows), best_sim, len(rows) > 1
        except Exception:
            logger.exception("识别@候选人失败")
            return False, -1, 0.0, False

    def _input_box_has_name(self, name: str) -> bool:
        """True if the input box contains `name` (or most of it) after @ pick.

        v5.0.1: 归一化比较 —— 微信候选面板选人后插入的是**成员显示名**
        ("Green Chennai"), 配置里的是群名片("Green_Chennai"), 下划线/空格/
        大小写差异必须抹掉再比(旧版只去空格, "Green_Chennai" 永远验证失败,
        导致唯一候选也不被认可、走粘贴降级)。
        """
        try:
            shot = self._get_screenshot()
            pos = self.get_input_box_position()
            if not shot or not pos:
                return False
            x, y = pos[0], pos[1]
            # 覆盖整个文本区（Weixin 4.x 输入框三行高、文字从顶部排），
            # 与 _input_box_signature 同口径
            region = shot.crop((max(0, x - 250), max(0, y - 80),
                                min(shot.width, x + 300), min(shot.height, y + 25)))
            texts = ocr_recognize(region)
            joined = "".join(t.get("text", "") for t in texts)
            n = _norm_id(name)
            j = _norm_id(joined)
            if not n:
                return False
            if n in j:
                return True
            tail = n[-3:]
            return bool(tail) and tail in j
        except Exception:
            return False

    def _verify_paste_arrived(self, pre_sig: Optional[bytes], label: str) -> bool:
        """粘贴前后输入框指纹必须有变化，否则内容根本没进框（粘贴失败/焦点丢失）。"""
        post = self._input_box_signature()
        if pre_sig is None or post is None:
            return True  # 看不清不拦
        diff = sum(1 for a, b in zip(post, pre_sig) if abs(a - b) > 18)
        if diff >= 3:
            return True
        logger.error("%s：粘贴后输入框无变化，内容未进框（放弃按 Enter，避免假发送）", label)
        return False

    def send_image_from_file(self, file_path: str, target: Optional[str] = None) -> bool:
        try:
            pos = self.get_input_box_position()
            if not pos:
                return False
            if not self._pre_compose_guard(target):
                self._clear_input_box()
                self.clear_pending_target()
                return False
            sx, sy = self._to_screen(pos[0], pos[1])
            self._human.click_at(sx, sy)
            time.sleep(0.3)
            pre_sig = self._input_box_signature()
            self._clipboard.paste_image_from_file(file_path)
            time.sleep(1.5)
            if not self._verify_paste_arrived(pre_sig, "图片粘贴"):
                self.clear_pending_target()
                return False

            if not self._assert_target(target):
                audit("发送图片", file_path, 结果="目标守卫拦截", 目标=target or self._pending_target)
                self._clear_input_box()
                self.clear_pending_target()
                return False

            ok = self._send_message()
            if not ok and self._own_bubble_sent():
                logger.info("图片发送复核：判定失败但聊天区底部已有绿色本人气泡 → 按已发送处理")
                ok = True
            time.sleep(1)
            if ok:
                logger.info("图片已从文件发送: %s", file_path)
                audit("发送图片", file_path, 方式="文件")
            else:
                audit("发送图片", file_path, 结果="发送未确认")
                self._clear_input_box()
            self.clear_pending_target()
            return ok
        except Exception:
            logger.exception("Failed to send image from file")
            self.clear_pending_target()
            return False

    def send_image_from_bytes(self, data: bytes, filename: str = "image.png", target: Optional[str] = None) -> bool:
        try:
            img = Image.open(io.BytesIO(data))
            pos = self.get_input_box_position()
            if not pos:
                return False
            if not self._pre_compose_guard(target):
                self._clear_input_box()
                self.clear_pending_target()
                return False
            sx, sy = self._to_screen(pos[0], pos[1])
            self._human.click_at(sx, sy)
            time.sleep(0.3)
            pre_sig = self._input_box_signature()
            self._clipboard.paste_image(img)
            time.sleep(1.5)
            if not self._verify_paste_arrived(pre_sig, "图片粘贴"):
                self.clear_pending_target()
                return False

            if not self._assert_target(target):
                audit("发送图片", filename, 结果="目标守卫拦截", 目标=target or self._pending_target)
                self._clear_input_box()
                self.clear_pending_target()
                return False

            ok = self._send_message()
            if not ok and self._own_bubble_sent():
                logger.info("图片发送复核：判定失败但聊天区底部已有绿色本人气泡 → 按已发送处理")
                ok = True
            time.sleep(1)
            if ok:
                logger.info("图片已从字节发送（%s）", filename)
                audit("发送图片", filename, 方式="字节", 大小=len(data))
            else:
                audit("发送图片", filename, 结果="发送未确认")
                self._clear_input_box()
            self.clear_pending_target()
            return ok
        except Exception:
            logger.exception("Failed to send image from bytes")
            self.clear_pending_target()
            return False

    def get_current_chat_name(self) -> Optional[str]:
        """当前打开的会话名（聊天区标题 OCR），读不到时返回 None。

        旧实现恒返回 None，导致 /api/status 的 current_chat 永远是空，
        排障时看不到"现在停在哪个会话"这个最关键的状态位。
        """
        try:
            title = self._chat_title()
            return title or None
        except Exception:
            logger.exception("读取当前会话名失败")
            return None
