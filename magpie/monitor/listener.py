"""WeChat unread message listener (v0.4.0 rewrite).

Previously this file ballooned into a "shit-mountain": scanning, badge
detection, plugin dispatch, idle browsing and OCR were all tangled together
with duplicated scroll / click logic.  This rewrite splits concerns cleanly:

- Sound-triggered ingress: WeChat plays a sound on a NEW message -> scan.
- Badge classification: big-red-dot+number = unread (open it); small-red-dot
  with no number = muted (消息免打扰) -> skip.
- Chat-list traversal: walk the list top->bottom once, only opening groups
  that truly have an unread count.
- Plugin pattern matching: a plugin declares (group + user + message regex);
  matching messages trigger the plugin's on_message handler.
- Continuous active-group monitoring: keep the top-activity group's chat
  window open, OCR it repeatedly (the OS focus stays on a browser window —
  WeChat is captured via PrintWindow even when not focused), and switch to
  another group when its activity score overtakes it.
- Idle browser simulation: when there are no tasks, drop focus and open 1-2
  web pages, all the while still OCR-ing the high-frequency chat window.

The public interface (running / get_listen_list / add_listen / remove_listen /
get_messages / start / stop / add_pattern / reload_patterns) is unchanged so
main.py, the web server and the OneBot layer keep working.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
import webbrowser
import collections
from collections import deque
from dataclasses import dataclass
from typing import Optional

from ..core.audit import audit
from ..core.op_queue import HOLD_UI_LOCK
from ..core.ocr import ChatListItem, ocr_recognize, scan_chat_list
from ..core.red_dot import BadgeInfo, classify_item_badge, detect_total_unread_badge
from ..core.target_guard import normalize
from ..core.ui_engine import UIEngine
from ..tui import bus
from .sound_monitor import AudioMeter

logger = logging.getLogger(__name__)

_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
_PURE_NAME_RE = re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9_·\-\s]{1,20}$")


# --------------------------------------------------------------------------
# Dedup & pattern matching
# --------------------------------------------------------------------------
class FingerprintCache:
    """会话内消息指纹缓存（跨重启去重的种子来自消息库，见 ADR-0002）。

    指针键 = (规范化会话名, 去空白文本)。**必须与提取路径共用同一个键口径**：
    主 OCR 路径与 glyph 单字符兜底路径历史上用过两个不同的键
    （normalize(sender) vs 原始 sender），导致同一条 "1" 回复被收两次 ——
    v5.0 起统一为一个键。FIFO 淘汰用 OrderedDict（旧版 set.pop() 随机淘汰，
    与"保留最近 N 条"的注释不符）。
    """

    def __init__(self, max_per_session: int = 800) -> None:
        self.max = max_per_session
        self._by_session: dict[str, "collections.OrderedDict[tuple, None]"] = {}

    def seen_key(self, contact: str, text: str) -> tuple:
        return (normalize(contact), re.sub(r"\s+", "", text or ""))

    def contains(self, contact: str, text: str) -> bool:
        return self.seen_key(contact, text) in self._by_session.setdefault(
            normalize(contact), collections.OrderedDict()
        )

    def add(self, contact: str, text: str) -> None:
        bucket = self._by_session.setdefault(normalize(contact), collections.OrderedDict())
        bucket[self.seen_key(contact, text)] = None
        while len(bucket) > self.max:
            bucket.popitem(last=False)  # FIFO：淘汰最旧指纹

    def seed(self, rows: list[dict]) -> int:
        """用消息库里的近期记录播种（跨重启去重）。"""
        n = 0
        for r in rows:
            contact = r.get("contact") or ""
            text = r.get("content") or ""
            if contact and text:
                self.add(contact, text)
                n += 1
        return n


class PatternMatcher:
    """Match messages against plugin-declared patterns."""

    def __init__(self) -> None:
        self._patterns: list[dict] = []

    def add_pattern(self, group: str = "", user: str = "", message: str = "", plugin_name: str = "") -> None:
        try:
            compiled = re.compile(message) if message else None
        except re.error:
            compiled = None
            logger.warning("插件 %s 的正则无效: %s", plugin_name, message)
        self._patterns.append({
            "group": group,
            "user": user,
            "message_pattern": compiled,
            "message_raw": message,
            "plugin": plugin_name,
        })

    def clear(self) -> None:
        self._patterns.clear()

    def match(self, group: str, user: str, message: str) -> list[str]:
        """group/user 用**规范化后相等**匹配，绝不用子串（防串群，见 target_guard）。

        旧版 `p["group"] not in group` 是子串匹配："测试" 会命中
        "测试的佛山投流工作群"，正是 target_guard 明令禁止的发错人前兆。
        """
        ng = normalize(group or "")
        nu = normalize(user or "")
        matched: list[str] = []
        for p in self._patterns:
            if p["group"] and normalize(p["group"]) != ng:
                continue
            if p["user"] and normalize(p["user"]) != nu:
                continue
            if p["message_pattern"] and not p["message_pattern"].search(message):
                continue
            matched.append(p["plugin"])
        return matched


# --------------------------------------------------------------------------
# Tracking state
# --------------------------------------------------------------------------
@dataclass
class ActiveSession:
    """A chat/group that is under continuous watch."""

    name: str
    unread: int = 0
    score: float = 0.0
    last_message: str = ""
    last_seen: float = 0.0

    def bump(self, weight: float = 1.0) -> None:
        self.score += weight
        self.last_seen = time.time()


class WeChatListener:
    """Smart listener: sound-triggered scan + continuous active-group OCR."""

    def __init__(self, config, engine: UIEngine, plugin_manager=None, store=None) -> None:
        self.config = config
        self.engine = engine
        self.adapter = engine.adapter
        self.pm = plugin_manager
        self._listening: set[str] = set(config.listen_contacts or [])
        self._monitor_all = getattr(config, "monitor_all", True)

        self._messages: deque[dict] = deque(maxlen=500)
        self._fingerprints = FingerprintCache()
        # 跨重启去重：用消息库里近期的收到记录播种指纹缓存
        if store is not None:
            try:
                seeded = self._fingerprints.seed(store.get_recent(limit=3000))
                logger.info("消息去重指纹已从消息库播种 %d 条", seeded)
            except Exception:
                logger.exception("消息去重播种失败")
        self._store = store  # magpie.core.store.MessageStore（收发同库）
        self._msg_seq = 0
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._matcher = PatternMatcher()
        self._load_plugin_patterns()

        self._sound = AudioMeter()
        # shared lock: the monitor scan and the send queue never run WeChat
        # UI at the same time (one operation at a time).
        self._ui_lock = HOLD_UI_LOCK
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # active-group tracking for continuous monitoring
        self._sessions: dict[str, ActiveSession] = {}
        self._current_session: Optional[str] = None
        self._idle_rounds = 0
        self._last_poke = 0.0
        self._last_refresh = 0.0
        self._recent_urls: set[str] = set()

        # probe the badge layout once (cache the chat-list region width)
        self._list_x2 = None

    # ------------------------------------------------------------- patterns
    def _load_plugin_patterns(self) -> None:
        if not self.pm:
            return
        for name, rec in self.pm.plugins.items():
            if not getattr(rec, "enabled", True):
                continue
            module = getattr(rec, "_module", None)
            if module is None:
                continue
            patterns = getattr(module, "PLUGIN", {}).get("monitor_patterns", [])
            for p in patterns:
                self._matcher.add_pattern(
                    group=p.get("group", ""),
                    user=p.get("user", ""),
                    message=p.get("message", ""),
                    plugin_name=name,
                )
                logger.info(
                    "插件 '%s' 注册监听: 群=%s 用户=%s 消息=%s",
                    name, p.get("group", "*"), p.get("user", "*"), p.get("message", "*") or "*",
                )

    def add_pattern(self, group: str = "", user: str = "", message: str = "", plugin_name: str = "") -> None:
        self._matcher.add_pattern(group, user, message, plugin_name)

    def reload_patterns(self) -> None:
        self._matcher.clear()
        self._load_plugin_patterns()

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._run())

        if self._sound.initialize():
            self._sound.set_cooldown(3.0)
            logger.info("声音触发已启用：微信提示音触发扫描")
        else:
            logger.info("声音触发不可用，使用定时轮询")

        mode = "全部会话" if self._monitor_all else f"{len(self._listening)} 个指定会话"
        logger.info("智能监听启动（%s，间隔 %ds）", mode, self.config.poll_interval_sec)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("智能监听已停止")

    @property
    def running(self) -> bool:
        return self._running

    # ------------------------------------------------------------- timing
    @staticmethod
    def _gauss_clamped(mean: float, std: float, lo: float, hi: float) -> float:
        while True:
            v = random.gauss(mean, std)
            if lo <= v <= hi:
                return v

    def _next_poll_delay(self) -> float:
        mean = max(1.0, float(self.config.poll_interval_sec))
        std = max(0.5, float(self.config.poll_interval_std_sec))
        return self._gauss_clamped(mean, std, 0.8, mean + 3 * std)

    def _react_delay(self) -> float:
        mean = max(0.4, float(getattr(self.config, "react_delay_ms", 1200)) / 1000.0)
        return self._gauss_clamped(mean, mean * 0.4, 0.25, mean * 2.2)

    # ------------------------------------------------------------- main loop
    async def _run(self) -> None:
        """Top-level orchestrator.

        Phase 1 - ingress: wait for a WeChat notification sound (or poll).
        Phase 2 - scan: traverse the chat list, open genuinely-unread groups,
                 collect their messages, dispatch to plugins, bump activity.
        Phase 3 - continuous: keep the most-active group open and OCR it
                 continuously, switching by activity, with browser idle in
                 between.
        """
        while self._running:
            try:
                triggered = await self._wait_for_trigger()
                if triggered:
                    # OCR / UI work blocks; run it off the event loop so the
                    # OneBot/Web servers and the TUI stay responsive.
                    await asyncio.to_thread(self._scan_all)

                # between scans, run a bounded burst of continuous monitoring
                # so a hot group keeps being OCR'd even when silent.
                burst_end = time.time() + self._continuous_burst_seconds()
                while self._running and time.time() < burst_end:
                    await asyncio.to_thread(self._continuous_tick_sync)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("监听主循环异常")
                await asyncio.sleep(1)

    def _schedule_dispatch(self, messages: list[dict]) -> None:
        """Schedule plugin dispatch back on the event loop (thread-safe)."""
        if not messages:
            return
        if self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(
                lambda: self._loop.create_task(self._dispatch_messages(messages))
            )
        except Exception:
            logger.exception("调度插件分发失败")

    def _persist_incoming(self, contact: str, m: dict) -> None:
        """收到的消息进统一消息库（ADR-0002）。store 未注入时只留在内存。"""
        if self._store is None:
            return
        try:
            self._store.log_receive(
                contact=contact,
                sender=m.get("sender", ""),
                content_type="text",
                content=(m.get("text") or "")[:1000],
            )
        except Exception:
            logger.exception("收到的消息入库失败")

    async def _wait_for_trigger(self) -> bool:
        """Wait for a WeChat sound OR an expired poll timer.

        Returns True when it is time to run a chat-list scan (either a sound
        was heard, or the periodic poll interval elapsed in fallback mode).
        """
        if self._sound.initialized:
            try:
                heard = await self._sound.wait_for_sound(timeout=self._next_poll_delay())
                if heard:
                    return True
            except Exception:
                pass
        else:
            await asyncio.sleep(self._next_poll_delay())
        # either sound-trigger (cooldown elapsed) or poll fallback: re-scan.
        return True

    def _continuous_burst_seconds(self) -> float:
        """How long to keep pushing continuous OCR between scans."""
        return max(3.0, float(getattr(self.config, "continuous_check_interval", 2.0)) * 4)

    def _continuous_tick_sync(self) -> None:
        """One continuous-monitoring tick, run in a worker thread.

        Blocks briefly (OCR + optional idle browsing) so it is offloaded with
        asyncio.to_thread; the event loop stays free for the servers / TUI.
        """
        top = self._pick_top_session()
        if top is None:
            self._idle_rounds += 1
            self._browse_when_idle()
        else:
            self._idle_rounds = 0
            self._monitor_session(top)
        if self._running:
            time.sleep(float(getattr(self.config, "continuous_check_interval", 2.0)))

    # ------------------------------------------------------------- scanning
    def _bring_wechat_front(self) -> None:
        """Bring WeChat to the foreground so clicks / scroll-wheel land on it.

        Losing focus is the #1 cause of the chat list not scrolling: the wheel
        events go to whatever window is on top under the cursor instead.
        """
        try:
            self.adapter._window.bring_to_front()
            time.sleep(0.15)
        except Exception:
            logger.exception("微信置顶失败")

    def _process_current_chat(self) -> None:
        """OCR and collect messages from the currently-open chat window.

        A very common case: the chat that just received a message is the one
        already shown in WeChat.  We click it once (focus), OCR the panel,
        save new (deduped) messages to CSV and dispatch to plugins.
        """
        name = self.adapter._chat_title()
        if not name or name == "(浏览期间)":
            logger.debug("当前无打开的会话，跳过当前会话处理")
            return
        self._bring_wechat_front()
        texts, region = self._ocr_chat_area()
        new_msgs = self._extract_messages(name, texts, region)
        for m in new_msgs:
            self._messages.append(m)
            self._persist_incoming(name, m)
        if new_msgs:
            sesh = self._sessions.setdefault(name, ActiveSession(name=name))
            sesh.bump(weight=float(getattr(self.config, "activity_boost", 3.0)))
            logger.info("当前会话 %s 收集 %d 条", name, len(new_msgs))
            audit("当前会话", name, 新增=len(new_msgs))
            self._schedule_dispatch(new_msgs)

    def _scan_all(self) -> None:
        """Traverse the chat list top->bottom; process unread (big-dot) chats.

        Strategy: the top-left total unread badge (N) tells us how many
        messages are still unread.  We keep scrolling/processing until that
        count reaches 0 (or we hit the bottom of the list).  Runs in a worker
        thread via asyncio.to_thread.
        """
        if not getattr(self.adapter, "_chat_list_region", None):
            logger.warning("聊天列表区域未定位，跳过扫描")
            return

        total = detect_total_unread_badge(self.adapter._get_screenshot())
        if total is None or total == 0:
            logger.debug("无未读提示")
            return

        self._list_x2 = self.adapter._chat_list_region[2]
        logger.info("检测到未读总数: %d", total)

        # 计数器在锁块**之前**初始化：旧版定义在 with 块内、for 循环前，
        # 若 _process_current_chat/_move_to_chat_list 抛异常，尾部日志行会
        # NameError，整轮扫描作废。
        processed_chats = 0
        processed_msgs = 0

        with self._ui_lock:
            self._bring_wechat_front()
            # 1) first handle the chat that is ALREADY open (most likely the one
            #    that just got a message).
            self._process_current_chat()
            self._move_to_chat_list()
            self._scroll_to_top()

            max_rounds = 25
            seen: set[str] = set()

            for _ in range(max_rounds):
                if not self._running:
                    break
                shot = self.adapter._get_screenshot()
                if not shot:
                    break
                items = scan_chat_list(shot, self.adapter._chat_list_region)
                if not items:
                    break

                round_had_unread = False
                for item in items:
                    if not self._running:
                        break
                    if not self._monitor_all and item.name not in self._listening:
                        continue
                    badge = self._classify_item(shot, item)
                    if badge.kind == "none":
                        continue
                    if badge.kind == "muted":
                        # 免打扰小红点 -> skip (no unread count)
                        logger.debug("免打扰（小红点无数字）跳过: %s", item.name)
                        continue
                    # unread, big red dot + number
                    round_had_unread = True
                    self._bring_wechat_front()
                    self._process_unread(item, badge)
                    processed_chats += 1
                    processed_msgs += badge.count
                    if processed_msgs >= total:
                        break

                if processed_msgs >= total:
                    logger.info("已处理到总未读数 %d，停止遍历", total)
                    break
                if not self._running:
                    break

                # if nothing unread was seen this round, re-read the badge: it may
                # already have dropped to 0 (everything read).
                if not round_had_unread:
                    remaining = detect_total_unread_badge(self.adapter._get_screenshot())
                    if remaining is None or remaining == 0:
                        break

                # scroll down; if the list stops changing we reached the bottom.
                self._bring_wechat_front()
                if not self._scroll_down_changed():
                    logger.info("聊天列表滚动到底部，停止遍历")
                    break

        if processed_chats > 0:
            logger.info("本轮扫描处理 %d 个会话 / %d 条消息", processed_chats, processed_msgs)
            audit("监听扫描", f"{processed_chats}个会话", 未读总数=total, 处理消息=processed_msgs)

    def _classify_item(self, shot, item: ChatListItem) -> BadgeInfo:
        try:
            return classify_item_badge(shot, item.x_position, item.y_position, item.width, item.height)
        except Exception:
            logger.exception("分类角标失败: %s", item.name)
            return BadgeInfo(kind="none")

    def _process_unread(self, item: ChatListItem, badge: BadgeInfo) -> None:
        """Open an unread group, jump to unread, OCR, dispatch, bump activity."""
        logger.info("处理未读会话: %s（%d 条未读）", item.name, badge.count)
        time.sleep(self._react_delay())

        # click the item, then VERIFY the chat switched (up to a few attempts).
        # CRITICAL: clicking an already-selected (green) item DESELECTS it and
        # blanks the content area, so we never re-click a selected item.
        before_hash = self.adapter._hash_chat_region()
        switched = False
        for attempt in range(4):
            self._bring_wechat_front()
            time.sleep(0.12)
            shot_now = self.adapter._get_screenshot()
            already = self.adapter._item_is_selected(shot_now, item.x_position, item.y_position)
            if already:
                if self.adapter._chat_selected(item.name, before_hash):
                    switched = True
                    break
                time.sleep(0.5)
                continue
            sx, sy = self.adapter._to_screen(item.x_position, item.y_position)
            self.adapter._human.click_at(sx, sy)
            time.sleep(1.0)
            if self.adapter._chat_selected(item.name, before_hash):
                switched = True
                break
            logger.info("会话未选中（当前标题: %s），重试 %s", self.adapter._chat_title() or "?", item.name)
            time.sleep(0.4)
        if not switched:
            logger.error("多次点击仍未切换到 '%s'，跳过该会话", item.name)
            return
        self._current_session = item.name

        self._click_jump_to_unread()

        session = self._sessions.setdefault(item.name, ActiveSession(name=item.name))
        session.unread = badge.count
        session.bump(weight=float(getattr(self.config, "activity_boost", 3.0)))

        pages = max(1, int(getattr(self.config, "scroll_pages", 3)))
        collected_msgs: list[dict] = []
        for p in range(pages):
            texts, region = self._ocr_chat_area()
            page_msgs = self._extract_messages(item.name, texts, region)
            for m in page_msgs:
                self._messages.append(m)
                self._persist_incoming(item.name, m)
            # 累计而不是覆盖：旧代码每轮给 new_msgs 重新赋值，循环结束后派发的
            # 只有**最后一页**。scroll_pages=3 时，若目标回复出现在第 1 页，
            # 后两页通常为空 → 回复永远发不出去；反之若每页都有内容，前面的
            # 页会被静默丢弃。
            collected_msgs.extend(page_msgs)
            if p < pages - 1:
                self._scroll_chat_down()

        collected = len(collected_msgs)
        # dispatch collected messages to plugins (replies are sent out)
        self._schedule_dispatch(collected_msgs)
        if collected:
            logger.info("已收集 %s 中 %d 条消息", item.name, collected)
        audit("监听会话", item.name, 未读=badge.count, 收集=collected)

    # ------------------------------------------------------------- continuous
    def _pick_top_session(self) -> Optional[str]:
        self._decay_scores()
        alive = [(n, s.score) for n, s in self._sessions.items() if s.score > 0.4]
        if not alive:
            return None
        alive.sort(key=lambda kv: kv[1], reverse=True)
        return alive[0][0]

    def _decay_scores(self) -> None:
        decay = float(getattr(self.config, "activity_decay", 0.85))
        for n, s in self._sessions.items():
            s.score *= decay
            if s.score <= 0.05:
                s.score = 0.0

    def _monitor_session(self, name: str) -> None:
        """Ensure this group's chat is open, then OCR it (continuous).

        Runs in a worker thread; blocks on search + OCR, so it is offloaded via
        asyncio.to_thread.  Plugin dispatch is scheduled back on the loop.
        """
        if name != self._current_session:
            with self._ui_lock:
                if not self._safe_search(name):
                    logger.warning("切换会话失败，放弃持续监听: %s", name)
                    self._sessions[name].score = 0.0
                    return
                self._current_session = name

        with self._ui_lock:
            texts, region = self._ocr_chat_area()
            new_msgs = self._extract_messages(name, texts, region)
            for m in new_msgs:
                self._messages.append(m)
                self._persist_incoming(name, m)
            if new_msgs:
                self._sessions[name].bump(weight=float(getattr(self.config, "activity_boost", 3.0)))
                logger.info("[持续监听] %s 新增 %d 条", name, len(new_msgs))
                audit("持续监听", name, 新增=len(new_msgs))
            self._schedule_dispatch(new_msgs)

        # keep the OS focus on a browser (WeChat is captured unfocused).
        self._poke_browser()

    def _safe_search(self, name: str) -> bool:
        try:
            return bool(self.adapter.search_contact(name))
        except Exception:
            logger.exception("搜索联系人异常: %s", name)
            return False

    # ------------------------------------------------------------- OCR/dispatch
    def _ocr_chat_area(self):
        """OCR the chat panel region. Returns (texts, region_image).

        The region image is needed so callers can classify message bubbles by
        colour (green = own message, grey = other people's).
        """
        shot = self.adapter._get_screenshot()
        if not shot:
            return [], None
        try:
            region = shot.crop(self.config.chat_region)
            texts = ocr_recognize(region)
            # a single-character reply (e.g. "1"/"0") is easy to miss at 1x;
            # run a 2x-upscaled pass and merge any extra detections.
            try:
                from PIL import Image
                big = region.resize((region.width * 2, region.height * 2), Image.LANCZOS)
                texts2 = ocr_recognize(big)
                for t in texts2:
                    if t.get("bbox"):
                        t["bbox"] = [[p[0] / 2, p[1] / 2] for p in t["bbox"]]
                texts.extend(texts2)
            except Exception:
                pass
            return texts, region
        except Exception:
            logger.exception("OCR 聊天区域失败")
            return [], None

    @staticmethod
    def _bubble_is_own(img, bbox: list) -> bool:
        """True if the OCR block sits in a GREEN (own) message bubble (#9DF29F).

        WeChat: your own messages have a green bubble (#9DF29F); other people's
        have a grey bubble (#EEEEF0).  We ignore our own (green) so the plugin's
        own @message is never mistaken for a reply.
        """
        if img is None:
            return False
        try:
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            cx = int(sum(xs) / len(xs))
            cy = int(sum(ys) / len(ys))
            x0, x1 = int(min(xs)), int(max(xs))
            y0, y1 = int(min(ys)), int(max(ys))
            px = img.load()
            green = 0
            total = 0
            # sample just above / below / beside the text (inside the bubble)
            for (x, y) in ((cx, y0 - 5), (cx, y1 + 5), (x0 - 5, cy), (x1 + 5, cy),
                           (cx, (y0 + y1) // 2)):
                if 0 <= x < img.width and 0 <= y < img.height:
                    r, g, b = px[x, y][:3]
                    total += 1
                    # #9DF29F-ish green
                    if g > 180 and (g - r) > 40 and (g - b) > 40:
                        green += 1
            return total > 0 and green >= max(1, total // 2)
        except Exception:
            return False

    def _extract_messages(self, sender: str, texts: list[dict], img=None) -> list[dict]:
        """Parse OCR blocks into messages, trying to attribute sender names.

        Green (own) bubbles are ignored (they are the messages WE sent). Grey
        (other people's) bubbles are kept — the @'ed member's reply is grey.

        Dedup key: the *canonical* session name (see :func:`target_guard.normalize`),
        NOT the raw string the caller happened to pass in.

        .. note::
            这是"同一条消息被处理两次导致插件重复回复"的根因修复。调用方给的
            sender 来自三个不同的数据源：``_process_current_chat`` 传聊天区
            标题 OCR、``_process_unread`` 传聊天列表 OCR 的 ``item.name``、
            ``_monitor_session`` 传会话字典的 key。OCR 噪声让同一个会话在这
            三个来源里长成不同的字符串（全角/半角 ＆、尾部 ``(12)``、长名截断），
            于是去重缓存里出现两个桶，同一条消息被提取两次 → 派发两次
            → 插件回复两条一模一样的内容。
        """
        cache = self._fingerprints
        new: list[dict] = []

        # detect sender prefix on the same visual row (name : message)
        rows: list[list[dict]] = []
        for t in sorted(texts, key=lambda t: (min(p[1] for p in t["bbox"]), min(p[0] for p in t["bbox"]))):
            y_top = min(p[1] for p in t["bbox"])
            if rows and abs(y_top - rows[-1][0]["y_top"]) <= 12:
                rows[-1].append(t)
            else:
                t["y_top"] = y_top
                rows.append([t])

        for row in rows:
            row = [t for t in row if (t["text"] or "").strip() and t["score"] >= 0.5]
            if not row:
                continue
            row.sort(key=lambda t: min(p[0] for p in t["bbox"]))
            text = row[0]["text"].strip()

            if not text or _TIME_RE.match(text):
                continue
            # skip WeChat placeholder segments like [图片] [链接]
            if re.match(r"^\[[^]]{1,6}\]$", text):
                continue
            # skip the plugin's own reply-instruction prompt lines, e.g.
            # "1/好的/OK = 受理" / "0/没空/pass = 跳过" / "请在5分钟内回复:".
            # Without this, OCR splitting the prompt would store "1"/"0" in the
            # dedup set and the REAL reply "1" would then be treated as a dup.
            if re.search(r"(请在[^。\n]{0,12}分钟内回复|/\s*[0-9]+\s*=|受理|跳过|\s=\s*(受理|跳过))", text):
                continue
            # skip OUR OWN (green-bubble) messages — they are what we sent
            if self._bubble_is_own(img, row[0]["bbox"]):
                logger.debug("跳过自己的(绿色)消息: %r", text)
                continue

            # 去重键用规范化后的会话名 + 去空白的消息文本，避免 OCR 噪声
            # （同一个会话名在不同数据源里写法不同）造成重复提取；
            # glyph 兜底路径共用同一指纹口径
            if cache.contains(sender, text):
                logger.debug("去重跳过（%s）: %r", sender, text[:30])
                continue
            cache.add(sender, text)
            self._msg_seq += 1

            # attribute sender: if the first block is short & pure-name-like and
            # more blocks follow, treat it as the sender label.
            user = ""
            if re.match(r".*[:：]$", text) and len(row) > 1:
                user = text.rstrip(":：").strip()
                joined = " ".join(t["text"].strip() for t in row[1:])
                if joined:
                    text = joined

            new.append({
                "id": self._msg_seq,
                "contact": sender,
                "text": text,
                "sender": user,
                "time": time.strftime("%H:%M:%S"),
            })
            logger.info("EXTRACT msg: contact=%r text=%r sender_attr=%r", sender, text, user)

        # --- glyph fallback: catch single-digit replies ("1"/"0") that the
        # text-detector drops.  Only run when the normal OCR found no reply
        # keyword (otherwise "ok" -> glyph "o" could be misread as reject "0").
        if img is not None:
            try:
                normal_reply = any(
                    (m["text"].strip().lower() in ("好的", "好", "可以", "收到", "没空", "跳过", "不接", "不要", "pass", "ok"))
                    or m["text"].strip() in ("1", "0")
                    for m in new
                )
                if not normal_reply:
                    from ..core.ocr import recognize_glyphs
                    reply_map = {"o": "0", "l": "1", "i": "1", "零": "0", "一": "1"}
                    reply_words = {"好的", "好", "可以", "收到", "接", "没空", "跳过", "不接", "不要", "pass", "ok"}
                    for g in recognize_glyphs(img):
                        gt = g["text"].strip().lower()
                        if gt in reply_map:
                            gt = reply_map[gt]
                        if gt not in reply_words and gt not in ("1", "0"):
                            continue
                        if self._bubble_is_own(img, g["bbox"]):
                            continue  # our own (green) message -> skip
                        if cache.contains(sender, gt):
                            continue
                        cache.add(sender, gt)  # 与主路径同一指纹口径（旧版两个键 → 重复收取）
                        self._msg_seq += 1
                        new.append({
                            "id": self._msg_seq,
                            "contact": sender,
                            "text": gt,
                            "sender": "",
                            "time": time.strftime("%H:%M:%S"),
                        })
                        logger.info("GLYPH reply: contact=%r text=%r", sender, gt)
            except Exception:
                logger.exception("GLYPH 兜底识别失败")
        return new

    async def _dispatch_messages(self, messages: list[dict]) -> None:
        """Dispatch new messages to plugins and send their replies.

        Gating: a plugin is notified when EITHER
          1) it declares `monitor_patterns` and one of them matches this
             message (group + user + message regex), OR
          2) it declares NO `monitor_patterns` (it always wants every message,
             backwards compatible with simple plugins like echo/help).
        """
        if not self.pm or not messages:
            return

        # what did patterns match?
        per_msg_matched: list[set[str]] = []
        for m in messages:
            per_msg_matched.append(set(self._matcher.match(m.get("contact", ""), m.get("sender", ""), m.get("text", ""))))

        for idx, m in enumerate(messages):
            group = m.get("contact", "")
            user = m.get("sender", "")
            text = m.get("text", "")
            matched = per_msg_matched[idx]
            logger.info("DISPATCH msg: contact=%r text=%r sender=%r matched=%r", group, text, user, matched)
            allowed = []
            for pname, rec in self.pm.plugins.items():
                module = getattr(rec, "_module", None)
                if module is None:
                    continue
                if module.PLUGIN.get("monitor_patterns"):
                    if pname in matched:
                        allowed.append(pname)
                else:
                    allowed.append(pname)

            ctx = {
                "event_type": "message",
                "message_type": "group" if "群" in group else "private",
                "contact": group,
                "message": text,
                "raw_message": text,
                "sender": {"name": user},
                "engine": self.engine,
                "matched_plugins": sorted(matched),
            }
            try:
                replies = await self.pm.dispatch_event("message", ctx, only=allowed)
                for seg in replies:
                    reply_text = (seg.get("data") or {}).get("text", "")
                    if reply_text and group:
                        await self.engine.send_text(group, reply_text)
                        audit("插件回复", group, 内容=reply_text[:50])
                        bus.record_op("插件回复", group, reply_text[:50], "ok")
            except Exception:
                logger.exception("插件分发消息出错")

    # ------------------------------------------------------------- browser
    @staticmethod
    def _url_keyword(url: str) -> str:
        """Derive a keyword from a URL's host that likely matches its page title."""
        try:
            from urllib.parse import urlparse

            host = (urlparse(url).hostname or "").lower()
        except Exception:
            host = ""
        if not host:
            return ""
        if "github" in host:
            return "github"
        if "news.qq" in host or host == "qq.com":
            return "腾讯"
        if "qq" in host:
            return "qq"
        if "baidu" in host:
            return "百度"
        if "bilibili" in host:
            return "bilibili"
        return host.replace("www.", "")

    def _is_browser_on_url(self, url: str) -> bool:
        """True if the foreground browser already shows `url`.

        We match the browser window title against a keyword derived from the
        URL host, so we don't spawn a duplicate tab for an already-open
        GitHub / 腾讯新闻 page.
        """
        keyword = self._url_keyword(url)
        if not keyword:
            return False
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return False
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return False
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return keyword.lower() in buf.value.lower()
        except Exception:
            return False

    def _open_url(self, url: str, new: int = 2) -> None:
        if not bus.is_browse_enabled():
            return
        if self._is_browser_on_url(url):
            logger.info("浏览器已在 %s，避免重复打开标签页", url)
            audit("浏览器复用", url, 目的="已打开，不重复建标签")
            return
        try:
            webbrowser.open(url, new=new)
        except Exception:
            logger.debug("打开浏览器页面失败: %s", url)

    def _browse_paused(self) -> bool:
        return not bus.is_browse_enabled()

    def _pick_urls(self) -> list[str]:
        """Pick 1-2 URLs to browse this cycle, avoiding repeats.

        Never repeats within a session and caps the count at 2.
        """
        pool = [u for u in (getattr(self.config, "browser_urls", []) or []) if u not in self._recent_urls]
        if not pool:
            pool = [u for u in (getattr(self.config, "browser_urls", []) or [])]  # wrap around
            self._recent_urls.clear()
        lo = max(1, int(getattr(self.config, "idle_browse_pages_min", 1)))
        hi = max(1, int(getattr(self.config, "idle_browse_pages_max", 2)))
        hi = min(hi, 2)          # never more than 2 pages (hard cap)
        count = min(random.randint(lo, hi), len(pool))
        picks = random.sample(pool, count)
        self._recent_urls.update(picks)
        return picks

    def _browse_page(self, url: str) -> None:
        """Open one page, then simulate human up/down scrolling and staggered
        F5 refresh while the browser stays foreground (WeChat stays behind)."""
        self._open_url(url, new=2)
        audit("空闲浏览", url, 目的="丢焦点·保持微信后台")
        stay = random.uniform(4.0, float(getattr(self.config, "browser_focus_sec", 8)))

        scroll = bool(getattr(self.config, "browser_scroll", True))
        scroll_interval = float(getattr(self.config, "browser_scroll_interval_sec", 6))
        last_scroll = time.time()
        last_refresh = self._last_refresh
        end = time.time() + stay

        refresh_min = max(60, int(getattr(self.config, "browser_refresh_min_sec", 1800)))
        refresh_max = max(refresh_min, int(getattr(self.config, "browser_refresh_max_sec", 3600)))

        while time.time() < end and not self._browse_paused() and self._running:
            now = time.time()
            if scroll and now - last_scroll >= scroll_interval:
                direction = "down" if random.random() >= 0.25 else "up"
                self.engine.human.page_scroll(direction, count=random.randint(1, 3))
                last_scroll = now
            if now - last_refresh >= random.uniform(refresh_min, refresh_max):
                self.engine.human.refresh_page()
                last_refresh = now
                break  # page refreshed; stay a little longer then move on
            time.sleep(random.uniform(0.8, 1.6))

        self._last_refresh = last_refresh

    def _poke_browser(self) -> None:
        """Re-foreground a browser page so WeChat is NOT focused (human-like).

        Throttled: opening a tab every continuous tick would explode the tab
        count, so we only touch the browser at most every `browser_focus_sec`
        seconds.  Skipped entirely while browsing is paused.
        """
        if self._browse_paused():
            return
        now = time.time()
        poke_every = max(5.0, float(getattr(self.config, "browser_focus_sec", 8)))
        if now - self._last_poke < poke_every:
            return
        self._last_poke = now
        urls = list(getattr(self.config, "browser_urls", []) or [])
        if urls:
            url = random.choice(urls)
            self._open_url(url, new=2)
            audit("浏览器焦点", url, 目的="保持微信后台")

    def _browse_when_idle(self) -> None:
        """No tasks -> drop focus and open 1-2 pages while still monitoring."""
        if self._browse_paused():
            return
        for url in self._pick_urls():
            self._browse_page(url)
            if self._browse_paused():
                break
        self._last_poke = time.time()  # reset throttle, next active tick can poke


    # ------------------------------------------------------------- navigation
    def _cursor_center(self):
        rx, ry, rw, rh = self.adapter._chat_list_region
        return (rx + rw) // 2, (ry + rh) // 2

    def _move_to_chat_list(self) -> None:
        cx, cy = self._cursor_center()
        self.adapter._human.move_mouse_to(cx, cy)
        time.sleep(random.uniform(0.2, 0.5))
        # wander the chat list a little (human browsing feel), vertical-biased,
        # staying inside the list bounds so it never interrupts the scroll.
        rx, ry, rw, rh = self.adapter._chat_list_region
        self.adapter._human.wander_region((rx, ry, rx + rw, ry + rh), seconds=random.uniform(0.5, 1.2))

    def _wheel_scroll(self, delta_y: int, ticks: int) -> None:
        """Scroll the (foregrounded) chat list using the mouse wheel.

        Uses the HUMAN simulator's scroll so the cursor drifts naturally, and
        brings WeChat to the front first so the wheel lands on the list.
        """
        cx, cy = self._cursor_center()
        self.adapter._human.scroll_list(cx, cy, direction="down" if delta_y < 0 else "up", amount=ticks)
        time.sleep(random.uniform(0.4, 0.8))

    def _scroll_to_top(self) -> None:
        self._bring_wechat_front()
        cx, cy = self._cursor_center()
        self.adapter._human.scroll_list(cx, cy, direction="up", amount=30)
        time.sleep(0.5)

    def _scroll_down(self, ticks: int = 4) -> None:
        self._bring_wechat_front()
        cx, cy = self._cursor_center()
        self.adapter._human.scroll_list(cx, cy, direction="down", amount=ticks)
        time.sleep(0.5)

    def _scroll_down_changed(self) -> bool:
        """Scroll the chat list down once; True if new content appeared."""
        before = self.adapter._get_screenshot()
        self._bring_wechat_front()
        cx, cy = self._cursor_center()
        self.adapter._human.scroll_list(cx, cy, direction="down", amount=4)
        time.sleep(0.5)
        after = self.adapter._get_screenshot()
        if before and after:
            before_names = {i.name for i in scan_chat_list(before, self.adapter._chat_list_region)}
            after_names = {i.name for i in scan_chat_list(after, self.adapter._chat_list_region)}
            if before_names == after_names:
                logger.debug("聊天列表滚动后无新内容")
                return False
        return True

    def _click_jump_to_unread(self) -> None:
        """Click the 'XX条新消息' jump button in the chat panel if present."""
        shot = self.adapter._get_screenshot()
        if not shot:
            return
        region = self.config.chat_region
        texts = ocr_recognize(shot.crop(region))
        for t in texts:
            if re.search(r"\d+\s*条新消息", t["text"]):
                xs = [p[0] for p in t["bbox"]]
                ys = [p[1] for p in t["bbox"]]
                cx = region[0] + int((min(xs) + max(xs)) / 2)
                cy = region[1] + int((min(ys) + max(ys)) / 2)
                sx, sy = self.adapter._to_screen(cx, cy)
                self.adapter._human.click_at(sx, sy)
                logger.info("点击『%s』跳转", t["text"])
                time.sleep(random.uniform(0.8, 1.4))
                return

    def _scroll_chat_down(self) -> None:
        self._bring_wechat_front()
        x1, y1, x2, y2 = self.config.chat_region
        cx, cy = self.adapter._to_screen((x1 + x2) // 2, (y1 + y2) // 2)
        self.adapter._human.scroll_list(cx, cy, direction="down", amount=random.randint(2, 4))
        time.sleep(random.uniform(0.5, 1.2))

    # ------------------------------------------------------------- listen API
    def add_listen(self, contact: str) -> bool:
        name = contact.strip()
        if not name:
            return False
        self._listening.add(name)
        if name not in self.config.listen_contacts:
            self.config.listen_contacts.append(name)
        logger.info("添加监听: %s", name)
        return True

    def remove_listen(self, contact: str) -> bool:
        name = contact.strip()
        if name in self._listening:
            self._listening.discard(name)
            if name in self.config.listen_contacts:
                self.config.listen_contacts.remove(name)
            logger.info("移除监听: %s", name)
            return True
        return False

    def get_listen_list(self) -> list[str]:
        return sorted(self._listening)

    def get_messages(self, limit: int = 50, since: int = 0) -> list[dict]:
        items = list(self._messages)
        if since:
            items = [m for m in items if m.get("id", 0) > since]
        return items[-limit:]
