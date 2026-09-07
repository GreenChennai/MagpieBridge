# -*- coding: utf-8 -*-
"""临时联调: 模拟 MsgPushWechat OneBot HTTP, 复用 leads_forwarder 调试 action,
验证 AI -> HTTP -> 插件中继 -> (模拟扩展) -> 结果 全链路。"""
import asyncio
import json
import sys
import importlib.util
from pathlib import Path

from aiohttp import web

PLUGIN = Path("D:/DLWechat/MagpieBridge/plugins/leads_forwarder/__init__.py")
spec = importlib.util.spec_from_file_location("lf_e2e", PLUGIN)
mod = importlib.util.module_from_spec(spec)
sys.modules["lf_e2e"] = mod
spec.loader.exec_module(mod)
mod.PLUGIN_SETTINGS = {"debug_enabled": True}


class FakeEngine:
    pass


async def handle_action(request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    action = body.get("action", request.match_info.get("action", ""))
    params = body.get("params", {})
    fn = getattr(mod, f"action_{action}", None)
    if not fn:
        return web.json_response({"status": "failed", "retcode": 100, "data": f"Unknown action: {action}"})
    try:
        result = fn(FakeEngine(), **params)
        if hasattr(result, "__await__"):
            result = await result
        return web.json_response({"status": "ok", "retcode": 0, "data": result})
    except Exception as e:
        return web.json_response({"status": "failed", "retcode": 200, "data": str(e)})


async def start_server():
    app = web.Application()
    app.router.add_post("/{action}", handle_action)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 3999)
    await site.start()
    print("MOCK OneBot HTTP on 127.0.0.1:3999")
    # 模拟扩展: 等 2 秒后开始轮询领取命令并"执行"
    async def fake_ext():
        await asyncio.sleep(2)
        import aiohttp as ah
        async with ah.ClientSession() as s:
            for i in range(3):
                async with s.post("http://127.0.0.1:3999/debug_poll", json={
                    "action": "debug_poll",
                    "params": {"tab_url": "https://life.douyin.com/p/liteapp/leads_cs/chat/session?groupid=1860525114613929",
                               "tab_title": "抖音来客-会话", "extension_version": "1.1.0"}}) as r:
                    data = (await r.json())["data"]
                cmd = data.get("command")
                if cmd:
                    print("  [fake-ext] claimed:", cmd["cmd_id"], cmd["cmd"], cmd["params"])
                    fake_result = {"cmd": cmd["cmd"], "fake": True,
                                   "url": "https://life.douyin.com/p/liteapp/leads_cs/chat/session?groupid=1860525114613929"}
                    async with s.post("http://127.0.0.1:3999/debug_result", json={
                        "action": "debug_result",
                        "params": {"cmd_id": cmd["cmd_id"], "result": fake_result}}) as r:
                        await r.json()
                    print("  [fake-ext] reported result for", cmd["cmd_id"])
                    break
                await asyncio.sleep(1)
    asyncio.get_event_loop().create_task(fake_ext())
    await asyncio.sleep(60)


if __name__ == "__main__":
    try:
        asyncio.run(start_server())
    except KeyboardInterrupt:
        pass
