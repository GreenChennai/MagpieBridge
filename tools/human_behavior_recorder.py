# -*- coding: utf-8 -*-
"""Human behavior recorder -> used to make the MagpieBridge mouse simulator realistic.

This tool does NOT guess.  It records how a REAL human moves/scrolls/clicks and
turns those measurements into a reusable behavior profile + sample simulation
code.

Two tasks:
  1. "按钮点击测试": a target button appears at a random spot; you move & click
     it.  We record the whole trajectory, speed, curve, pauses, click dwell.
  2. "聊天列表查找测试": a fake WeChat-like chat list with random members; a
     target is announced; you find (scroll if needed) & click it.  We record find
     time, scroll behaviour, and the approach trajectory.

Run:  python human_behavior_recorder.py
```

Windows only (uses Win32 GetCursorPos).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import math
import random
import statistics
import threading
import time
from collections import deque
from pathlib import Path

import tkinter as tk
from tkinter import messagebox

user32 = ctypes.windll.user32

_OUT = Path(__file__).resolve().parent / "human_profile.json"


# ---------------------------------------------------------------- cursor sampler
class CursorSampler:
    """Polls the real cursor at high frequency (Win32 GetCursorPos)."""

    def __init__(self, hz=80.0):
        self.hz = hz
        self._stop = False
        self.buf: deque[tuple[float, float, float]] = deque(maxlen=6000)  # (t, x, y)
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        pt = ctypes.wintypes.POINT()
        interval = 1.0 / self.hz
        while not self._stop:
            user32.GetCursorPos(ctypes.byref(pt))
            self.buf.append((time.time(), pt.x, pt.y))
            time.sleep(interval)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop = True

    def snap(self) -> list[tuple[float, float, float]]:
        return list(self.buf)


# ---------------------------------------------------------------- profile utils
def analyz(path: list[tuple[float, float, float]]) -> dict:
    """Analyze a recorded trajectory (t0, x, y ...) into summary stats.

    path is a list of (t, x, y).  We compute total distance, duration, average
    speed, peak speed, curvature (bezier-throw), tremors (residual after a
    smoothed line), and count/mean of pauses > 80ms.
    """
    if len(path) < 3:
        return {}
    pts = path
    dist = 0.0
    for i in range(1, len(pts)):
        dist += math.hypot(pts[i][1] - pts[i - 1][1], pts[i][2] - pts[i - 1][2])
    dur = max(pts[-1][0] - pts[0][0], 1e-6)
    avg_speed = dist / dur

    # velocity samples -> peak
    speeds = []
    for i in range(1, len(pts)):
        d = math.hypot(pts[i][1] - pts[i - 1][1], pts[i][2] - pts[i - 1][2])
        dt = max(pts[i][0] - pts[i - 1][0], 1e-6)
        speeds.append(d / dt)
    peak = max(speeds) if speeds else 0.0

    # pauses: consecutive samples with dt > 0.08s
    pauses = []
    for i in range(1, len(pts)):
        dt = pts[i][0] - pts[i - 1][0]
        if dt > 0.08:
            pauses.append(dt)
    pause_mean = statistics.mean(pauses) * 1000 if pauses else 0.0

    # tremor: deviation from a straight line start->end
    x0, y0 = pts[0][1], pts[0][2]
    x1, y1 = pts[-1][1], pts[-1][2]
    L = math.hypot(x1 - x0, y1 - y0) or 1.0
    dev = []
    for (_, px, py) in pts:
        # perpendicular distance from line
        num = abs((y1 - y0) * px - (x1 - x0) * py + x1 * y0 - y1 * x0)
        dev.append(num / L)
    tremor = statistics.pstdev(dev) if dev else 0.0

    return {
        "distance": round(dist, 1),
        "duration_ms": round(dur * 1000, 0),
        "avg_speed": round(avg_speed, 2),
        "peak_speed": round(peak, 2),
        "pause_count": len(pauses),
        "pause_mean_ms": round(pause_mean, 0),
        "tremor_px": round(tremor, 2),
    }


# ---------------------------------------------------------------- recorder app
class RecorderApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Human Behavior Recorder")
        self.root.geometry("760x560")
        self.sampler = CursorSampler()

        self.mode = tk.StringVar(value="buttons")
        self.running = False
        self.recording = []
        self.current_path = []

        self.moves: list[dict] = []
        self.clicks: list[dict] = []
        self.searches: list[dict] = []

        self._build_ui()

    # ------------------------------------------------------- UI
    def _build_ui(self):
        top = tk.Frame(self.root)
        top.pack(fill="x", padx=10, pady=8)
        tk.Radiobutton(top, text="按钮点击测试", variable=self.mode, value="buttons",
                       command=self._mode_changed).pack(side="left", padx=6)
        tk.Radiobutton(top, text="聊天列表查找测试", variable=self.mode, value="chat",
                       command=self._mode_changed).pack(side="left", padx=6)
        tk.Button(top, text="开始录制", command=self.start).pack(side="left", padx=6)
        tk.Button(top, text="统计/生成模拟代码", command=self.done).pack(side="left", padx=6)
        tk.Button(top, text="重置", command=self.reset).pack(side="left", padx=6)

        self.status = tk.Label(self.root, text="选择任务后点「开始录制」", fg="#555")
        self.status.pack(fill="x", padx=10)

        self.canvas = tk.Canvas(self.root, bg="#f4f5f7", highlightthickness=1, highlightbackground="#ccc")
        self.canvas.pack(fill="both", expand=True, padx=10, pady=10)
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind_all("<MouseWheel>", self.on_wheel)

        self.log = tk.Text(self.root, height=7, state="disabled", bg="#111", fg="#0f0", font=("Consolas", 9))
        self.log.pack(fill="x", padx=10, pady=(0, 8))

    def _log(self, msg):
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _mode_changed(self):
        self.reset()

    # ------------------------------------------------------- start / stop
    def start(self):
        self.running = True
        self.current_path = []
        if self.mode.get() == "buttons":
            self._start_buttons()
        else:
            self._start_chat()

    def reset(self):
        self.running = False
        self.canvas.delete("all")
        self.status.config(text="已重置，请选择任务后点「开始录制」")
        self.moves, self.clicks, self.searches = [], [], []
        self._log("重置完成")

    # ------------------------------------------------------- buttons task
    def _start_buttons(self):
        self.canvas.delete("all")
        self.status.config(text="点击出现的按钮（每点完一次会换位置，共 10 次）")
        self._round = 0
        self._spawn_button()

    def _spawn_button(self):
        if self._round >= 10:
            self.running = False
            self.status.config(text="按钮测试完成，点「统计/生成模拟代码」")
            return
        self.canvas.delete("target")
        w, h = self.canvas.winfo_width() or 640, self.canvas.winfo_height() or 460
        bx, by = random.randint(80, max(120, w - 80)), random.randint(40, max(80, h - 40))
        self.button_pos = (bx, by)
        b = tk.Button(self.canvas, text="目标", width=8, height=2, command=self._button_hit)
        self.canvas.create_window(bx, by, window=b, tags="target")

    def _button_hit(self):
        if not self.running:
            return
        self._record_move_and_click(self.button_pos, hit=True)
        self._round += 1
        self._spawn_button()

    # ------------------------------------------------------- chat task
    def _start_chat(self):
        self.canvas.delete("all")
        self.status.config(text="从列表中找出目标并点击（可用滚轮滚动，共 3 轮）")
        self._chat_round = 0
        self._spawn_chat()

    def _spawn_chat(self):
        if self._chat_round >= 3:
            self.running = False
            self.status.config(text="聊天列表测试完成，点「统计/生成模拟代码」")
            return
        self.canvas.delete("all")
        w, h = self.canvas.winfo_width() or 640, self.canvas.winfo_height() or 460
        names = [
            "张三", "李四", "王五", "赵六", "孙七", "周八", "吴九", "郑十",
            "陈晓铭", "安信德", "创客龙", "黄小明", "陈晓铭2", "安信德2",
            "客户A", "客户B", "销售一组", "销售二组", "林琳", "刘强东",
        ] * 3
        random.shuffle(names)
        self._offset = 0
        self._target = random.choice([n for n in names if n not in ("陈晓铭2", "安信德2")])
        self._names = names
        self._target_y = None
        self._chat_h = h
        self._chat_t0 = time.time()
        self._render_chat()
        self.status.config(text=f"请点击：{self._target}（第 {self._chat_round+1}/3 轮）")

    def _render_chat(self):
        self.canvas.delete("rows")
        y = 10 - self._offset
        self.row_positions = []
        idx = 0
        while y < self._chat_h and idx < len(self._names):
            name = self._names[idx]
            row = self.canvas.create_rectangle(10, y, self.canvas.winfo_width() - 10, y + 40,
                                               fill="#ffffff", outline="#eee", tags="rows")
            self.canvas.create_oval(18, y + 6, 46, y + 34, fill=self._av_color(name), tags="rows")
            self.canvas.create_text(56, y + 20, anchor="w", text=name, font=("Microsoft YaHei", 11), tags="rows")
            self.row_positions.append({"name": name, "y": y, "row": idx})
            y += 44
            idx += 1
        self.canvas.tag_bind("rows", "<Button-1>", self._chat_row_click)

    def _av_color(self, name):
        c = sum(ord(ch) for ch in name)
        return "#%02x%02x%02x" % (100 + (c % 100), 120 + (c % 80), 150 + (c % 60))

    def _chat_row_click(self, event):
        if not self.running:
            return
        target_row = None
        for r in self.row_positions:
            if r["y"] - 6 <= event.y <= r["y"] + 46:
                target_row = r
                break
        if not target_row:
            return
        hit = target_row["name"] == self._target
        self._record_move_and_click((target_row["y"], target_row["row"]), hit=hit, chat=target_row)
        if hit:
            self._log(f"✓ 点击正确：{self._target}")
            self._chat_round += 1
            self._spawn_chat()
        else:
            self._log(f"✗ 点错（{target_row['name']}），目标仍是 {self._target}，再试")

    def on_wheel(self, event):
        if self.mode.get() == "chat" and self.running:
            delta = -1 if event.delta > 0 else 1
            self._offset += delta * 30
            self._offset = max(0, min(self._offset, max(0, len(self._names) * 44 - self._chat_h)))
            self._render_chat()

    # ------------------------------------------------------- capture
    def on_press(self, _e):
        self._press_t = time.time()

    def on_click(self, event):
        # tk clicks on canvas may land on created windows; record the release
        pass

    def on_release(self, event):
        pass

    def _record_move_and_click(self, pos, hit=True, chat=None):
        pts = self.sampler.snap()
        # trajectory: from a bit before the press to now
        now = time.time()
        path = [p for p in pts if p[0] <= now]
        if len(path) >= 3:
            lead = path[-30:]
            stats = analyz(lead)
        else:
            stats = {}
        dwell = (time.time() - self._press_t) * 1000 if hasattr(self, "_press_t") else 0
        item = {"pos": list(pos), "hit": hit, "dwell_ms": round(dwell, 0), "traj": stats}
        if chat:
            # find time = time since task started
            item["find_after_ms"] = self._chat_round_elapsed()
        self._log(f"记录 {item}")
        self.clicks.append(item)
        if stats and stats.get("distance", 0) > 4:
            self.moves.append(stats)

    def _chat_round_elapsed(self):
        return getattr(self, "_chat_t0", 0) and int((time.time() - self._chat_t0) * 1000) or 0

    def _set_chat_t0(self):
        self._chat_t0 = time.time()

    # ------------------------------------------------------- done / analyze
    def done(self):
        profile = self._build_profile()
        json.dump(profile, open(_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        self._log(f"已写入 {_OUT}")
        code = self._generate_code(profile)
        self._log("\n========== 生成的模拟参考代码 ==========\n" + code)
        self.canvas.delete("all")
        self.canvas.create_text(20, 20, anchor="nw", text=code, fill="#333", font=("Consolas", 9))

    def _build_profile(self) -> dict:
        """FLAT profile (key names match HumanSimulator._profile reads)."""
        mv = self.moves
        avg = lambda k, d: round(statistics.mean([m[k] for m in mv if k in m]), 2) if mv else d
        cl = self.clicks
        dw = [c["dwell_ms"] for c in cl if c.get("dwell_ms") and c["dwell_ms"] < 1000] if cl else []
        profile = {
            "source": "human_behavior_recorder",
            "avg_speed_px_s": avg("avg_speed", 0),
            "peak_speed_px_s": avg("peak_speed", 0),
            "avg_tremor_px": avg("tremor_px", 0),
            "click_dwell_ms": round(statistics.mean(dw), 0) if dw else 150,
        }
        return profile

    def _generate_code(self, p: dict) -> str:
        s = f'''# 由 HumanBehaviorRecorder 基于真人操作实测生成（sample）
# 可直接参考/并入 HumanSimulator
HUMAN_PROFILE = {{
    "avg_speed_px_s": {p.get("avg_speed_px_s", 169)},
    "peak_speed_px_s": {p.get("peak_speed_px_s", 480)},
    "tremor_amplitude_px": {p.get("avg_tremor_px", 2.0)},
    "pause_probability": 0.0,
    "pause_mean_ms": 0.0,
    "click_dwell_ms": {p.get("click_dwell_ms", 150)},
}}

def human_move_to(x, y):
    """按实测特征生成一次拟人移动（贝塞尔 + 实测速度/停顿/抖动）。"""
    import random, math, time
    dist = math.hypot(x, y)
    duration = max(0.25, dist / HUMAN_PROFILE["avg_speed_px_s"])
    steps = max(6, int(duration * 60))
    cx = x + random.randint(-20, 20); cy = y + random.randint(-20, 20)
    for i in range(steps):
        t = i / steps
        px = (1-t)**2*0 + 2*(1-t)*t*cx + t*t*x
        py = (1-t)**2*0 + 2*(1-t)*t*cy + t*t*y
        jx = random.uniform(-HUMAN_PROFILE["tremor_amplitude_px"], HUMAN_PROFILE["tremor_amplitude_px"])
        jy = random.uniform(-HUMAN_PROFILE["tremor_amplitude_px"], HUMAN_PROFILE["tremor_amplitude_px"])
        set_cursor(px + jx, py + jy)
        if random.random() < HUMAN_PROFILE["pause_probability"]:
            time.sleep(HUMAN_PROFILE["pause_mean_ms"] / 1000)
        time.sleep(duration / steps)
'''
        return s

    def run(self):
        self.sampler.start()
        self.root.mainloop()
        self.sampler.stop()


if __name__ == "__main__":
    RecorderApp().run()
