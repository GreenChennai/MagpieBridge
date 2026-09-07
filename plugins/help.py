# -*- coding: utf-8 -*-
"""示例插件：help —— 列出当前已加载的插件。

演示：
1. 事件处理 `on_message`：收到 "帮助" / "help" / "插件" 时列出插件清单
2. 通过 ctx 中的 engine 访问插件管理器

安装：复制本文件到程序目录 Plugins/ 文件夹，重启或调用
POST /api/plugins/reload 加载。
"""

PLUGIN = {
    "name": "help",
    "version": "1.0.0",
    "description": "回复插件清单（示例插件）",
    "author": "MagpieBridge",
    "events": ["message"],
    "actions": [],
}


async def on_message(ctx):
    msg = (ctx.get("message") or "").strip()
    if msg not in ("帮助", "help", "插件", "plugins"):
        return []

    engine = ctx.get("engine")
    pm = getattr(engine, "plugin_manager", None)
    if pm is None:
        return [{"type": "text", "data": {"text": "插件系统未启用"}}]

    lines = ["已加载插件："]
    for p in pm.get_plugin_list():
        status = "启用" if p["enabled"] else "禁用"
        lines.append(f"- {p['name']} v{p['version']} [{status}] {p['description']}")
        if p["error"]:
            lines.append(f"  ⚠ 加载错误: {p['error']}")
    return [{"type": "text", "data": {"text": "\n".join(lines)}}]
