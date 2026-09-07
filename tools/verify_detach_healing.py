"""断开 RDP 后的自愈验证探针(ADR-0006 现场验证)。

用户断开远程桌面后运行:每 2 秒记录一次三信号探针 + 微信窗口状态,
共 90 秒,输出时间线;然后触发一次真实发送,核对消息库状态。

用法: python tools/verify_detach_healing.py [--send]
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from magpie.core.session_control import probe_desktop  # noqa: E402

REPORT = Path(r"D:\DLWechat\断开自愈验证报告.md")
TEST_GROUP = "测试的佛山投流工作群"


def watch(seconds: int = 90, interval: float = 2.0) -> list[dict]:
    timeline = []
    deadline = time.time() + seconds
    print(f"观察 {seconds}s(每 {interval:.0f}s 一次)……", flush=True)
    while time.time() < deadline:
        p = probe_desktop()
        p["t"] = time.strftime("%H:%M:%S")
        timeline.append(p)
        print(f"[{p['t']}] state={p['session_state']} desktop={p['input_desktop']} fg={p['foreground']}", flush=True)
        time.sleep(interval)
    return timeline


def healing_summary(timeline: list[dict]) -> str:
    states = [r["session_state"] for r in timeline]
    desktops = [r["input_desktop"] for r in timeline]
    fgs = [r["foreground"] for r in timeline]
    lines = [
        f"- 观测样本:{len(timeline)} 个(覆盖 {len(timeline) * 2}s)",
        f"- 会话状态序列:{states}",
        f"- 输入桌面可达:{sum(desktops)}/{len(desktops)} 次",
        f"- 有前台窗口:{sum(1 for f in fgs if f)}/{len(fgs)} 次",
    ]
    return "\n".join(lines)


def send_test() -> dict:
    body = json.dumps(
        {"contact": TEST_GROUP, "message": "【ADR-0006 验证】断开远程桌面后自动发送测试,请忽略。"},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:8080/api/send/text",
        data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    return json.loads(urllib.request.urlopen(req, timeout=240).read().decode())


def main() -> int:
    send = "--send" in sys.argv
    print("=== 阶段 1:观察会话自愈 ===", flush=True)
    timeline = watch(90)
    summary = healing_summary(timeline)
    print(summary, flush=True)

    result = ""
    if send:
        print("=== 阶段 2:真实发送验证 ===", flush=True)
        try:
            r = send_test()
            result = f"发送 API 返回:`{json.dumps(r, ensure_ascii=False)}`"
            print(result, flush=True)
        except Exception as e:
            result = f"发送 API 异常:`{e}`"
            print(result, flush=True)

    REPORT.write_text(
        "# 断开 RDP 自愈验证报告(ADR-0006)\n\n"
        f"生成时间:{time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"## 探针时间线\n\n{summary}\n\n"
        f"## 发送验证\n\n{result or '(未执行,加 --send 参数)'}\n",
        encoding="utf-8",
    )
    print(f"报告已写入 {REPORT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
