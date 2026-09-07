r"""断开 RDP 全自动验证编排(ADR-0006 现场验证,无人值守)。

流程:
1. 轮询三信号探针,等待"断开"事件(state=DISCONNECTED 或 fg==0 持续);
2. 断开后等 20s,让宿主(提权实例)的 5s 巡检自动 tscon 挂回控制台;
3. 探针确认自愈结果(桌面可达/有前台/会话 Active);
4. 通过 Web API 触发一次真实发送到指定测试群,核对消息库状态;
5. 全程时间线写入 D:\DLWechat\断开自愈验证报告.md。

用法: python tools/auto_verify_detach.py [最长等待秒数,默认1800]
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from magpie.core.session_control import WTS_DISCONNECTED, probe_desktop  # noqa: E402

REPORT = Path(r"D:\DLWechat\断开自愈验证报告.md")
TEST_GROUP = "测试的佛山投流工作群"
log_lines: list[str] = []


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    log_lines.append(line)
    print(line, flush=True)


def api_send(contact: str, message: str, timeout: int = 240) -> dict:
    body = json.dumps({"contact": contact, "message": message}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:8080/api/send/text",
        data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())


def api_messages(contact: str, limit: int = 3) -> list[dict]:
    req = urllib.request.Request(
        f"http://127.0.0.1:8080/api/messages?limit={limit}&contact={urllib.parse.quote(contact)}"
    )
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode())


import urllib.parse  # noqa: E402


def main() -> int:
    max_wait = int(sys.argv[1]) if len(sys.argv) > 1 else 1800
    log("=== 编排启动:等待断开事件 ===")
    log("探针基线: " + json.dumps(probe_desktop()))

    # 阶段 1:等待断开(state=DISCONNECTED 或 桌面不可达 或 fg==0)
    detached_at = None
    deadline = time.time() + max_wait
    while time.time() < deadline:
        p = probe_desktop()
        if p["session_state"] == WTS_DISCONNECTED or not p["input_desktop"] or not p["foreground"]:
            detached_at = time.time()
            log(f"检测到断开态: {json.dumps(p)}")
            break
        time.sleep(3)
    if detached_at is None:
        log("等待超时,用户未断开。写入基线报告后退出。")
        REPORT.write_text("# 断开自愈验证报告\n\n(等待超时,未检测到断开)\n", encoding="utf-8")
        return 1

    # 阶段 2:给宿主巡检(5s)+ tscon + 桌面重渲染 留时间
    log("断开确认,等待 25s 让宿主自动挂回控制台……")
    time.sleep(25)

    # 阶段 3:自愈确认(最多再等 60s)
    healed = False
    for i in range(12):
        p = probe_desktop()
        ok = p["input_desktop"] and p["foreground"] and p["session_state"] != WTS_DISCONNECTED
        log(f"自愈探针 #{i + 1}: {json.dumps(p)} -> {'OK' if ok else '等待'}")
        if ok:
            healed = True
            break
        time.sleep(5)
    log(f"自愈结论: {'成功' if healed else '未在窗口期内恢复'}")
    # tscon 挂回后宿主会自动息屏 → fg 可能变 0(背光关不影响合成输入)。
    # 所以以「输入桌面可达 + 会话非断开」为自愈金标准:
    p = probe_desktop()
    healed_final = p["input_desktop"] and p["session_state"] != WTS_DISCONNECTED
    log(f"金标准复核(桌面可达+会话活跃): {json.dumps(p)} -> {healed_final}")

    # 阶段 4:真实发送
    send_result = ""
    try:
        log(f"触发真实发送 -> {TEST_GROUP}")
        r = api_send(TEST_GROUP, "【ADR-0006 验证】断开远程桌面后自动发送测试,请忽略。")
        send_result = json.dumps(r, ensure_ascii=False)
        log(f"发送 API: {send_result}")
        time.sleep(2)
        rows = api_messages(TEST_GROUP, 3)
        if rows:
            send_result += f" | 消息库最新状态: {rows[0].get('status')} (id={rows[0].get('id')})"
            log(send_result)
    except Exception as e:
        send_result = f"异常: {e}"
        log(send_result)

    verdict = (
        "✅ 通过:断开后自动挂回控制台,发送链路正常"
        if healed_final and '"success": true' in send_result
        else "❌ 未完全通过(详见时间线)"
    )
    REPORT.write_text(
        "# 断开 RDP 自愈验证报告(ADR-0006)\n\n"
        f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"## 结论\n\n{verdict}\n\n"
        f"## 发送验证\n\n`{send_result}`\n\n"
        "## 时间线\n\n```\n" + "\n".join(log_lines) + "\n```\n",
        encoding="utf-8",
    )
    log(f"完成。报告: {REPORT}")
    return 0 if healed_final else 2


if __name__ == "__main__":
    raise SystemExit(main())
