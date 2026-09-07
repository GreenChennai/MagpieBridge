# 插件开发指南

## 最小插件

在 `plugins/my_plugin.py`(或 `plugins/my_plugin/__init__.py`)写入:

```python
PLUGIN = {
    "name": "my_plugin",
    "version": "1.0.0",
    "description": "示例插件",
    "events": ["message"],                      # 订阅事件
    "monitor_patterns": [                       # 可选:过滤哪些消息派发给本插件
        {"group": "我的群", "user": "", "message": ".*关键词.*"},
    ],
    "actions": ["ping"],                        # OneBot 自定义 action
    "settings": [                               # 后台「插件设置」表单
        {"key": "greeting", "type": "text", "default": "你好"},
    ],
}

async def on_message(ctx):
    # ctx 字段:contact / message / raw_message / sender{name} /
    #          engine / matched_plugins
    if "关键词" in ctx["message"]:
        return [{"type": "text", "data": {"text": "收到!"}}]
    return []          # 返回空 = 不回复;返回消息段 = 自动回复到原会话

def action_ping(engine, **params):
    # 经 OneBot action 调用:POST /ping {"params": {...}}
    return {"ok": True, "params": params}
```

保存后任选其一生效:后台「插件 → 重载插件」、`POST /api/plugins/reload`、TUI 按 `r`。

## 约定

- **事件**:`on_<事件名>`(当前支持 `message`),可同步或 async,异常被隔离不影响其他插件;
- **动作**:`action_<名>`,第一个参数注入 `engine`,其余参数来自 OneBot `params`;
- **设置**:`PLUGIN_SETTINGS` 由宿主注入(dict);文件 `plugins/config/<名>.json` 白名单键过滤、人手可改、保存即热生效;
- **group 匹配是"规范化后相等"**,绝不做子串(防「测试」命中「测试的佛山投流工作群」)。

## 发送三态(重要)

```python
state = await ctx["engine"].send_text_state(contact, text)
# "sent"      确认已发送
# "failed"    确认未发出(守卫拦截/执行失败)→ 可安全重试
# "uncertain" 结果不确定(超时)→ 禁止盲目重发,记录并人工确认
```

图片 / @ 同理:`send_image_bytes_state`、`send_at_message_state`。
老的两态接口 `send_text` 等仍然可用(`== "sent"`)。

## 官方范例

`plugins/leads_forwarder/`(LeadsLinker V3)是"如何写一个完整插件"的参考实现:
事件订阅 + monitor_patterns、13 个 OneBot action、12 项设置 schema、
三态消费与重试策略、CSV 台账、热更新配置。见 [[LeadsLinker-V3]]。
