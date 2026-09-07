# -*- coding: utf-8 -*-
"""示例插件：echo / ping。

演示两种能力：
1. 事件处理 `on_message`：收到 "ping" 回复 "pong"；收到 "echo xxx" 回复 "xxx"
2. OneBot action `action_ping`：可通过 OneBot HTTP/WS 调用 `ping` action

安装：把本文件复制到程序目录下的 Plugins/ 文件夹，重启程序或调用
POST /api/plugins/reload 即可加载。
"""

PLUGIN = {
    "name": "echo",
    "version": "1.0.0",
    "description": "回复 ping/pong 与 echo 内容（示例插件）",
    "author": "MagpieBridge",
    "events": ["message"],
    "actions": ["ping"],
}


async def on_message(ctx):
    """消息事件处理函数。

    参数 ctx: 事件上下文（dict）
        - event_type:   事件类型，固定为 "message"
        - message_type: 消息类型，微信场景为 "private"（暂为占位）
        - contact:      来源会话名（微信联系人/群名）
        - message:      消息文本
        - raw_message:  原始消息文本
        - sender:       发送者信息（dict，预留）

    返回值: 回复列表（list），每个元素是一条消息段
        [{"type": "text", "data": {"text": "..."}}]
        返回空列表表示不回复。
    """
    msg = (ctx.get("message") or "").strip()
    if msg == "ping":
        return [{"type": "text", "data": {"text": "pong"}}]
    if msg.startswith("echo "):
        return [{"type": "text", "data": {"text": msg[5:]}}]
    return []


async def action_ping(engine, **params):
    """OneBot 自定义 action：测试连通性。

    通过 OneBot HTTP 调用：
        POST http://127.0.0.1:3000/ping
        {"action": "ping"}
    通过 WebSocket 发送：{"action": "ping", "echo": 1}

    参数 engine: UIEngine 实例（可用 engine.send_text(contact, text) 发消息）
    返回值: 任意 JSON 可序列化的数据。
    """
    return {"ok": True, "pong": True, "echo": params}
