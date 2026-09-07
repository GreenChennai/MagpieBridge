"""MagpieBridge Textual TUI.

Replaces the plain console CLI with a full terminal UI:

    +--------------------------------------------------------------+
    |  状态: 运行中  | 运行: 123s  | 消息: 42  | 网页浏览: 进行       |
    |  微信窗口: 已连接 | 初始化: 就绪 | 微信: 4.1.13 | 当前会话: —    |
    |  OneBot HTTP: http://127.0.0.1:3000  WS: ...  Web: ... [打开后台]|
    +----------------------+---------------------------------------+
    |  运行日志             |  操作日志 [AUDIT]                      |
    |  (left RichLog)      |  (right RichLog)                      |
    +----------------------+---------------------------------------+
    |  操作队列 (bottom DataTable)                                   |
    +--------------------------------------------------------------+

Everything above is rendered as Chinese.  The app is async-native and runs in
the same asyncio loop as the OneBot/web servers and the monitor listener.
Core modules publish events through `tui/bus.py`; this app drains them on a
timer.  The panels are seeded from the log file + a welcome note so they are
never blank, and each sub-refresh is isolated so one failure can't starve the
others.
"""

from __future__ import annotations

import logging
import webbrowser
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical
from textual.widgets import Button, DataTable, Footer, Header, RichLog, Static

from . import bus

logger = logging.getLogger("magpie.tui")

STATUS_CN = {
    "running": "运行中",
    "degraded": "降级运行",
    "monitor_off": "监听未启用",
    "initializing": "初始化",
    "stopped": "已停止",
}

# 秒数 → "1y 2d 3h 30m 24s" 智能单位（非零单位从大到小，空格分隔）
_UPTIME_UNITS = (("y", 31536000), ("d", 86400), ("h", 3600), ("m", 60), ("s", 1))


def fmt_uptime(seconds) -> str:
    sec = max(0, int(seconds or 0))
    parts = []
    for unit, size in _UPTIME_UNITS:
        if sec >= size:
            val, sec = divmod(sec, size)
            parts.append(f"{val}{unit}")
    if not parts:
        return "0s"
    return " ".join(parts)

# ---------------------------------------------------------------- CSS
CSS = """
Screen {
    background: #0d1117;
    color: #e6edf3;
}

#app-grid {
    layout: grid;
    grid-size: 1 3;
    grid-rows: auto 1fr auto;
    grid-columns: 1fr;
}

/* ---- top status strip ---- */
#status-strip {
    height: auto;
    background: #161b22;
    border-bottom: heavy #30363d;
    padding: 0 1;
}
#status-strip Static {
    height: auto;
    min-height: 1;
    padding: 0 1;
    color: #8b949e;
    text-wrap: wrap;   /* wrap long endpoint/status text instead of clipping */
}
#status-strip #status-main { color: #58a6ff; text-style: bold; }
#status-strip .ok { color: #3fb950; }
#status-strip .warn { color: #d29922; }
#status-strip .err { color: #f85149; }

#open-web {
    width: auto;
    min-width: 12;
    height: 2;              /* show the label (a 1-row text button clips it) */
    min-height: 2;
    margin: 0 0 1 1;
    padding: 0 3;
    background: #238636;
    color: white;
    text-style: bold;
}
#open-web:focus { background: #2ea043; }
#open-web:hover { background: #2ea043; }

#status-row1 {
    layout: horizontal;
    height: auto;
    min-height: 1;
    align-vertical: middle;
}
#status-row1 #status-main {
    width: 1fr;
    height: auto;
}

/* ---- bottom action bar (打开后台 / q退出 / r重载插件 / s开关网页浏览) ----
   紧凑布局：按钮按内容宽度左对齐排一行（height:1），不再等宽拉满 */
#bottom-bar {
    layout: horizontal;
    height: 2;
    min-height: 2;
    background: #161b22;
    border-top: heavy #30363d;
    padding: 0 1;
    align-vertical: middle;
}
#bottom-bar Button {
    width: auto;
    height: 1;
    min-height: 1;
    margin: 0 1 0 0;
    padding: 0 2;
    background: #21262d;
    color: #c9d1d9;
}
#bottom-bar Button:hover { background: #30363d; }
#bottom-bar Button:focus { background: #30363d; }
/* "开始挂起"用橙色突出（其余按钮保持灰蓝） */
#bottom-bar #btn-hang {
    background: #9e6a03;
    color: #ffffff;
}
#bottom-bar #btn-hang:hover { background: #b07b07; }
#bottom-bar #btn-hang:focus { background: #b07b07; }
#bottom-bar #open-web {
    background: #238636;
    color: white;
    text-style: bold;
}
#bottom-bar #open-web:hover { background: #2ea043; }
#bottom-bar #open-web:focus { background: #2ea043; }
#bottom-bar .btn-danger { color: #f85149; }

/* ---- body (logs) ---- */
#body {
    layout: grid;
    grid-size: 2 1;
    grid-columns: 3fr 2fr;
    height: 1fr;
    min-height: 4;
    padding: 0;
}
.pane {
    width: 1fr;
    height: 1fr;
    min-height: 4;
    background: #0d1117;
    border: heavy #30363d;
}
.pane > .pane-title {
    height: 1;
    min-height: 1;
    background: #21262d;
    color: #c9d1d9;
    padding: 0 1;
    text-style: bold;
}
.pane RichLog {
    height: 1fr;
    min-height: 3;
    background: #0d1117;
    padding: 0 1;
    overflow-x: hidden;   /* wrap, never run off the right edge */
}
#log-panel { color: #c9d1d9; }
#action-panel { color: #8b949e; }

/* ---- bottom operation queue ---- */
#ops-pane {
    height: 11;
    min-height: 5;
    background: #161b22;
    border: heavy #30363d;
    margin-top: 1;
    padding: 0 1;
}
#ops-pane > .pane-title {
    height: 1;
    min-height: 1;
    background: #21262d;
    color: #c9d1d9;
    padding: 0 1;
    text-style: bold;
}
#ops-table {
    height: 1fr;
    min-height: 3;
    background: #0d1117;
}
DataTable { height: 1fr; }
DataTable > .datatable--header {
    background: #21262d;
    color: #58a6ff;
    text-style: bold;
}

Footer { background: #161b22; color: #8b949e; }
"""


# ---------------------------------------------------------------- App
class MagpieTui(App[None]):
    """Textual app rendering MagpieBridge's live state (Chinese UI)."""

    TITLE = "MagpieBridge v5.0.0"
    SUB_TITLE = "微信消息推送 & OneBot v11"
    CSS = CSS
    # 防止窗口被拖到过小导致布局计算异常（旧版 conhost 拖拽崩溃的诱因之一）
    MIN_SIZE = (80, 20)

    BINDINGS = [
        Binding("q", "quit", "退出", show=True),
        Binding("r", "reload", "重载插件", show=True),
        Binding("s", "toggle_browse", "开关网页浏览", show=True),
        # 消费 ctrl+alt+p：避免 Textual / 任何控件把该组合键当作"搜索/命令面板"
        # 打开一个搜索列表（用户反馈的干扰）。真正的延时操作已改用 ctrl+alt+y。
        Binding("ctrl+alt+p", "noop", "", show=False),
    ]

    def __init__(
        self,
        engine=None,
        monitor=None,
        msg_logger=None,
        listener=None,
        plugin_manager=None,
        on_mount_cb=None,
    ) -> None:
        super().__init__()
        self.engine = engine
        self.monitor = monitor
        self.msg_logger = msg_logger
        self.listener = listener
        self.plugin_manager = plugin_manager
        self.on_mount_cb = on_mount_cb
        self._last_audit_ts = 0.0
        # RichLog 在 write 时按当时宽度 wrap、resize 后不会自动重排；
        # 记录上次写入宽度，变化时重写历史行让换行与面板边界同步。
        # 注意：必须用 widget 总宽（panel.size.width）做信号——scrollable
        # 区域宽度会随滚动条出现/消失 ±1 抖动，用它做信号会导致每秒
        # clear+重写循环（一直闪烁）。
        self._log_wrap_width: Optional[int] = None
        # 操作队列上次渲染的签名（时间+状态+目标+详情），内容没变就不重建，
        # 避免每秒 clear+add_row 导致光标/滚动位置被重置（内容乱跳）。
        self._ops_sig: Optional[tuple] = None

    # ---------------------------------------------------------- compose
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with Vertical(id="status-strip"):
            with Horizontal(id="status-row1"):
                yield Static("", id="status-main")
            yield Static("", id="status-engine")
            yield Static("", id="status-net")

        with Grid(id="body"):
            with Vertical(id="left", classes="pane"):
                yield Static("运行日志", classes="pane-title")
                # markup=True: log lines carry [color] markup built in log_handler.py
                # min_width=0: 默认 min_width=78 会强制把行 wrap 到 78 列（比窄面板
                # 内容区还宽），导致右侧内容被裁、换行与面板边界不同步。
                # max_lines: RichLog 默认无上限，长时间运行内存会无限增长；
                # 与 bus._log_history(maxlen=2000) 对齐，超出丢弃最旧行。
                yield RichLog(id="log-panel", wrap=True, highlight=False, markup=True, min_width=0, max_lines=2000)
            with Vertical(id="right", classes="pane"):
                yield Static("操作日志", classes="pane-title")
                # markup=False: audit text may contain arbitrary '[...]' brackets
                yield RichLog(id="action-panel", wrap=True, highlight=False, markup=False, min_width=0, max_lines=500)

        with Vertical(id="ops-pane"):
            yield Static("操作队列", classes="pane-title")
            yield DataTable(id="ops-table", cursor_type="row", zebra_stripes=False)

        # 底部操作栏：打开后台(绿) / q退出 / r重载插件 / s开关网页浏览 / 开始挂起 / 延时操作
        # compact=True 去掉按钮自带边框，height:1 时才不会把文字挤没
        with Horizontal(id="bottom-bar"):
            yield Button("打开后台", id="open-web", variant="success", compact=True)
            yield Button("q 退出", id="btn-quit", compact=True)
            yield Button("r 重载插件", id="btn-reload", compact=True)
            yield Button("s 开关网页浏览", id="btn-browse", compact=True)
            yield Button("开始挂起 Ctrl+Alt+E", id="btn-hang", compact=True)
            yield Button("延时操作 Ctrl+Alt+Y", id="btn-delay", compact=True)

    def on_mount(self) -> None:
        self._init_ops_table()
        self._seed_log_panel()
        self.set_interval(1.0, self._refresh)
        # let the host reposition the console after the TUI takes over
        if callable(self.on_mount_cb):
            try:
                self.on_mount_cb()
            except Exception:
                pass

    def _init_ops_table(self) -> None:
        table = self.query_one("#ops-table", DataTable)
        table.clear(columns=True)
        for col in ("时间", "类型", "目标", "详情", "状态"):
            table.add_column(col, width=12)
        table.cursor_coordinate = (0, 0)

    def _seed_log_panel(self) -> None:
        """Pre-load the log panel so it is never blank.

        Only drains the in-memory bus buffer (colored compact lines from
        TuiLogHandler).  Deliberately does NOT fall back to reading the log
        file: RichLog.write during on_mount is deferred (size not yet known)
        so ``panel.lines`` is always empty here — an ``if not panel.lines``
        check would therefore read the file *every* startup, mixing the file's
        full-format lines (with ms + module name, no color) together with the
        same events arriving in real time as colored compact lines — the
        "double log" the user reported.
        """
        panel = self.query_one("#log-panel", RichLog)
        lines = bus.drain_log()
        if lines:
            for line in lines:
                panel.write(line)
        else:
            panel.write("— MagpieBridge 已启动，等待事件 … —")
        # 记录当前宽度，避免首次 _refresh_log 立即触发一次无谓的全量重写
        self._log_wrap_width = self._log_panel_width(panel)

    @staticmethod
    def _log_panel_width(panel: RichLog) -> int:
        """The wrap width RichLog actually uses when writing lines.

        Use the widget TOTAL width (`panel.size.width`) as the re-wrap signal.
        `scrollable_content_region.width` is the true wrap width, but it shrinks
        by 1 when a scrollbar appears and grows back when it disappears — using
        it as the trigger makes the width oscillate (scrollbar appears → rewrite
        → more/fewer wrapped lines → scrollbar disappears → rewrite → …) and the
        panel flickers forever.  Widget width only changes on a real resize, so
        it is a stable trigger.  The rewrite itself still wraps at the current
        scrollable width via RichLog.write, so lines stay exact to ±1 column.
        """
        try:
            return int(panel.size.width)
        except Exception:
            return 0

    # ---------------------------------------------------------- refresh
    def _refresh(self) -> None:
        # each sub-refresh is isolated so one failing panel can't starve the
        # others.
        for fn in (self._refresh_status, self._refresh_log, self._refresh_audit, self._refresh_ops):
            try:
                fn()
            except Exception:
                logger.exception("TUI 面板刷新失败")

    def _refresh_status(self) -> None:
        main = self.query_one("#status-main", Static)
        eng = self.query_one("#status-engine", Static)
        net = self.query_one("#status-net", Static)

        st = bus.get_status()
        status = st.get("status", "initializing")
        status_cn = STATUS_CN.get(status, status)
        status_color = {"running": "ok", "degraded": "warn", "monitor_off": "warn"}.get(status, "warn")
        browse_on = bus.is_browse_enabled()
        main.update(
            f"状态: [{status_color}]{status_cn}[/{status_color}]"
            f"    运行时长: {fmt_uptime(st.get('uptime_seconds', 0))}"
            f"    已处理消息: {st.get('messages_sent', 0)}"
            f"    浏览网页: [{'ok' if browse_on else 'warn'}]{'开启' if browse_on else '关闭'}[/]"
        )

        if self.engine is not None:
            try:
                es = self.engine.get_status()
                found = es.get("window_found", False)
                init = es.get("initialized", False)
                ver = es.get("wechat_version", "?")
                chat = es.get("current_chat", "") or "—"
                eng.update(
                    f"微信窗口: [{'ok' if found else 'err'}]{'已连接' if found else '未连接'}[/]"
                    f"    初始化: [{'ok' if init else 'warn'}]{'就绪' if init else '未就绪'}[/]"
                    f"    微信: {ver}    当前会话: {chat}"
                )
            except Exception:
                eng.update("微信状态: 读取失败")

        http = st.get("onebot_http", "—")
        ws = st.get("onebot_ws", "—")
        web = st.get("web_url", "—")

        listening = False
        if self.listener is not None:
            try:
                listening = self.listener.running
            except Exception:
                listening = False
        net.update(
            f"监听: [{'ok' if listening else 'warn'}]{'运行中' if listening else '停止'}[/]"
            f"   OneBot HTTP: {http}   OneBot WS: {ws}   管理后台: {web}"
        )

    def _refresh_log(self) -> None:
        panel = self.query_one("#log-panel", RichLog)
        # RichLog wraps each line at write time and never re-wraps after a
        # resize — on a narrow window the right part of previously written
        # lines is clipped.  Detect a width change and re-seed from the bus
        # history so every line re-wraps to the current panel width.
        # The signal is the widget width (see _log_panel_width) so scrollbar
        # toggling can never trigger a rewrite loop.
        width = self._log_panel_width(panel)
        if width and width != self._log_wrap_width:
            self._log_wrap_width = width
            history = bus.get_log_history()
            if history:
                # clear() resets virtual_size → scroll clamps to top, then each
                # write() auto-scrolls to bottom: without restoring the position
                # every rewrite yanks the view (flicker + "content jumps").
                try:
                    at_bottom = panel.scroll_y >= panel.max_scroll_y - 1
                    saved_y = panel.scroll_y
                except Exception:
                    at_bottom, saved_y = True, 0
                panel.clear()
                for line in history:
                    panel.write(line, scroll_end=False)
                try:
                    if at_bottom:
                        panel.scroll_end(animate=False)
                    else:
                        panel.scroll_to(panel.scroll_x, min(saved_y, panel.max_scroll_y), animate=False)
                except Exception:
                    pass
        lines = bus.drain_log()
        for line in lines:
            # follow the bottom while the user is at the bottom; never yank the
            # view away when they scrolled up to read history.
            try:
                follow = panel.scroll_y >= panel.max_scroll_y - 1
            except Exception:
                follow = True
            panel.write(line, scroll_end=follow)

    def _refresh_audit(self) -> None:
        try:
            from ..core.audit import get_audit_log

            entries = get_audit_log(limit=200)
            panel = self.query_one("#action-panel", RichLog)
            if not entries:
                if not panel.lines:
                    panel.write("系统运行中，暂无操作事件…")
                return
            newest = entries[0]["ts"]
            if newest > self._last_audit_ts:
                for entry in reversed(entries):
                    if entry["ts"] <= self._last_audit_ts:
                        break
                    params = " ".join(f"{k}={v}" for k, v in entry.get("params", {}).items())
                    gap = entry.get("gap_ms", "")
                    # plain text (no markup) so display can never be swallowed
                    panel.write(
                        f"{entry.get('operation', '')}   {entry.get('target', '')}   {params}"
                        + (f"  间隔{gap}ms" if gap else "")
                        + f"   [{entry.get('time', '')}]"
                    )
                self._last_audit_ts = newest
        except Exception:
            pass

    def _refresh_ops(self) -> None:
        table = self.query_one("#ops-table", DataTable)
        ops = bus.get_ops(limit=60)
        # 数据没变就不重建：每秒 clear+add_row 会把光标/滚动位置重置回顶部，
        # 用户查看队列时内容会"跳走"。用签名判断，只有变化才重建。
        sig = tuple(
            (o.get("time", ""), o.get("kind", ""), o.get("target", ""),
             o.get("detail", ""), o.get("status", ""))
            for o in ops
        )
        if sig == self._ops_sig and table.row_count > 0:
            return
        self._ops_sig = sig
        try:
            cur = table.cursor_coordinate
        except Exception:
            cur = None
        if table.row_count > 0:
            table.clear()
        if not ops:
            table.add_row("", "—", "—", "暂无操作（等待消息 / 发送任务）", "")
            return
        for op in ops:
            status = op.get("status", "running")
            color = "#33d69f" if status == "ok" else ("#d29922" if status == "running" else "#f85149")
            sn = {"ok": "完成", "running": "进行中", "fail": "失败"}.get(status, status)
            table.add_row(op["time"], op["kind"], op["target"], op["detail"], f"[{color}]{sn}[/{color}]")
        # 重建后恢复原光标位置，避免跳到第一行
        if cur is not None and table.row_count > 0:
            try:
                table.cursor_coordinate = (
                    min(cur.column, len(table.columns) - 1),
                    min(cur.row, table.row_count - 1),
                )
            except Exception:
                pass

    def on_button_pressed(self, event) -> None:
        bid = event.button.id
        if bid == "open-web":
            self.action_open_web()
        elif bid == "btn-quit":
            self.action_quit()
        elif bid == "btn-reload":
            self.action_reload()
        elif bid == "btn-browse":
            self.action_toggle_browse()
        elif bid == "btn-hang":
            self.action_hang()
        elif bid == "btn-delay":
            self.action_delay()

    # ---------------------------------------------------------- actions
    def action_open_web(self) -> None:
        web = bus.get_status().get("web_url", "http://127.0.0.1:8080")
        try:
            webbrowser.open(web, new=2)
            self.notify(f"已打开管理后台: {web}", severity="information")
        except Exception as e:
            self.notify(f"打开后台失败: {e}", severity="error")

    def action_toggle_browse(self) -> None:
        enabled = bus.toggle_browse()
        self.notify("模拟网页浏览已" + ("开启" if enabled else "关闭"), severity="information")

    def action_hang(self) -> None:
        """开始挂起：把会话挂回本机控制台（断开远程桌面），保持桌面可交互。"""
        try:
            from ..core.session_control import hang_to_console
            ok, msg = hang_to_console()
            self.notify(msg, severity="information" if ok else "error")
            bus.record_note("开始挂起: " + msg)
        except Exception as e:
            self.notify(f"开始挂起失败: {e}", severity="error")

    def action_delay(self) -> None:
        """延时操作：让出鼠标控制权 30 秒，期间发送/浏览暂停抢鼠标。"""
        bus.yield_to_user(30)
        self.notify("已让出鼠标控制 30 秒（延时操作），可操作其它窗口", severity="information")

    def action_noop(self) -> None:
        """消费 ctrl+alt+p，防止其触发搜索/命令面板（不执行任何动作）。"""
        pass

    def action_reload(self) -> None:
        if self.plugin_manager is None:
            self.notify("插件系统未启用", severity="warning")
            return
        try:
            result = self.plugin_manager.reload_plugins()
            self.notify(f"已重载插件，共 {result.get('count', 0)} 个", severity="information")
            bus.record_note("插件重载完成")
        except Exception as e:
            self.notify(f"插件重载失败: {e}", severity="error")
