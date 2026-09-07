# -*- coding: utf-8 -*-
"""临时测试: 验证 leads_forwarder 调试通道中继逻辑 (不依赖 MsgPushWechat 运行时)。"""
import asyncio
import sys
import importlib.util
from pathlib import Path

PLUGIN = Path("D:/DLWechat/MagpieBridge/plugins/leads_forwarder/__init__.py")
spec = importlib.util.spec_from_file_location("lf_test", PLUGIN)
mod = importlib.util.module_from_spec(spec)
sys.modules["lf_test"] = mod
spec.loader.exec_module(mod)

# 模拟 MsgPushWechat 注入的插件设置
mod.PLUGIN_SETTINGS = {"debug_enabled": True}


class FakeEngine:
    pass


async def main():
    engine = FakeEngine()

    # 1. status 初始状态
    st = await mod.action_debug_status(engine)
    assert st["ok"] and st["pending"] == 0 and st["extension_connected"] is False
    print("1. status ok:", st)

    # 2. submit 一条命令
    sub = await mod.action_debug_submit(engine, cmd="snapshot", params={"max": 5})
    assert sub["ok"] and sub["cmd_id"].startswith("dbg_")
    print("2. submit ok:", sub)

    # 3. 扩展轮询领取
    poll = await mod.action_debug_poll(engine, tab_url="https://life.douyin.com/x", tab_title="t", extension_version="1.1.0")
    assert poll["command"] and poll["command"]["cmd"] == "snapshot"
    print("3. poll claim ok:", poll["command"]["cmd_id"])
    assert mod._debug_heartbeat["url"] == "https://life.douyin.com/x"

    # 4. query 一站式: 提交->扩展领取->回传->返回结果
    async def fake_ext_cycle():
        await asyncio.sleep(0.3)
        poll = await mod.action_debug_poll(engine)
        assert poll["command"] and poll["command"]["cmd"] == "snapshot"
        await mod.action_debug_result(engine, cmd_id=poll["command"]["cmd_id"],
                                      result={"url": "https://life.douyin.com/"})

    task = asyncio.create_task(fake_ext_cycle())
    q = await mod.action_debug_query(engine, cmd="snapshot", timeout=5)
    await task
    assert q["status"] == "done" and q["result"]["url"].endswith("life.douyin.com/")
    print("4. query done ok:", q["cmd_id"], q["status"])

    # 5. 无扩展领取 -> query 超时返回 timeout
    q2 = await mod.action_debug_query(engine, cmd="html", params={"selector": "body"}, timeout=1)
    assert q2["status"] == "timeout" and q2["cmd_id"]
    print("5. query timeout ok:", q2["cmd_id"])

    # 6. 扩展之后再领取并回传 -> fetch 可取
    poll2 = await mod.action_debug_poll(engine)
    assert poll2["command"] and poll2["command"]["cmd"] == "html"
    cid2 = poll2["command"]["cmd_id"]
    await mod.action_debug_result(engine, cmd_id=cid2, result={"count": 1})
    f = await mod.action_debug_fetch(engine, cmd_id=cid2)
    assert f["status"] == "done" and f["result"]["count"] == 1
    print("6. delayed fetch ok:", f)

    # 7. cancel
    sub3 = await mod.action_debug_submit(engine, cmd="walk")
    c = await mod.action_debug_cancel(engine, cmd_id=sub3["cmd_id"])
    assert c["ok"]
    f3 = await mod.action_debug_fetch(engine, cmd_id=sub3["cmd_id"])
    assert f3["status"] == "pending"
    print("7. cancel ok")

    # 8. debug_enabled=false 时拒绝 submit
    mod.PLUGIN_SETTINGS = {"debug_enabled": False}
    sub4 = await mod.action_debug_submit(engine, cmd="snapshot")
    assert not sub4["ok"] and "未开启" in sub4["error"]
    print("8. disabled reject ok:", sub4)

    print("\nALL DEBUG RELAY TESTS PASSED")


asyncio.run(main())
