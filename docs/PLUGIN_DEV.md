# MagpieBridge 插件开发文档 (v0.4.0)

> 基于 OneBot v11 的插件系统。把插件放进程序目录的 `Plugins/` 文件夹即可加载，
> 无需改主程序。本文面向**插件开发者（人或 AI）**，照做即可写出可用插件。

---

## 1. 快速开始（5 分钟）

### 1.1 插件目录结构

MagpieBridge 会**自动创建** `Plugins/` 文件夹。支持两种布局（可共存）：

```
MagpieBridge.exe
└── Plugins/                              ← 插件目录
    ├── echo.py                           ← 单文件插件（简单场景）
    └── leads_forwarder/                  ← 文件夹插件（推荐：可拆模块/放资源）
        ├── __init__.py                   ← 插件入口（含 PLUGIN 元信息）
        └── renderer.py                   ← 子模块（from .renderer import ...）
```

- **单文件插件**：`Plugins/xxx.py`，插件名 = 文件名（去掉 `.py`）。
- **文件夹插件**：入口为 `__init__.py`（或 `main.py`），插件名 = **文件夹名**；
  同目录其他 `.py` 可用 `from .xxx import ...` 导入；资源文件用
  `Path(__file__).parent` 定位。
- 文件名/文件夹名 **不要以 `_` 开头**（会被忽略）。

### 1.2 安装 / 热加载

1. 新建 `Plugins/my_first_plugin.py`
2. 重启程序，或调用接口热加载：
   ```bash
   POST http://127.0.0.1:8080/api/plugins/reload
   ```
3. 查看加载结果：`GET http://127.0.0.1:8080/api/plugins`

### 1.3 最小插件

```python
# -*- coding: utf-8 -*-
# Plugins/my_first_plugin.py

PLUGIN = {
    "name": "my_first_plugin",   # 插件唯一名（必填）
    "version": "1.0.0",
    "description": "我的第一个插件",
    "author": "",
    "events": ["message"],       # 监听的事件（目前支持 "message"）
    "actions": [],               # 提供的 OneBot action 名
}

async def on_message(ctx):
    msg = (ctx.get("message") or "").strip()
    if msg == "你好":
        # 返回值 = OneBot v11 消息段列表；返回 [] 表示不回复
        return [{"type": "text", "data": {"text": "你好呀！"}}]
    return []
```

---

## 2. `PLUGIN` 元信息

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `name` | str | ✅ | 插件唯一标识，用于启用/禁用/排查 |
| `version` | str | ✅ | 版本号，如 `"1.0.0"` |
| `description` | str | - | 插件功能说明（列表/控制台展示） |
| `author` | str | - | 作者 |
| `events` | list[str] | - | 监听的事件，目前支持 `["message"]` |
| `actions` | list[str] | - | 提供的 OneBot 自定义 action 名 |
| `monitor_patterns` | list[dict] | - | 消息监控匹配模式（见 2.1） |
| `settings` | list[dict] | - | 后台可配置设置项（见 2.4） |
| `requires` | list[str] | - | 必需第三方依赖包名（见 2.3） |
| `enabled` | bool | - | 默认是否启用（缺省 True） |

### 2.1 `monitor_patterns`（消息监控模式）

插件**声明**它关心哪些消息。当系统检测到一条消息满足（`group` AND `user`
AND `message` 全部匹配）时，才会触发 `on_message`。这是"某群某人的某类消息
才触发"的关键机制。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `group` | str | 群名/联系人（**部分匹配**；空 = 匹配所有） |
| `user` | str | 发送者名（**部分匹配**；空 = 匹配所有） |
| `message` | str | 消息内容**正则**（空 = 匹配所有） |

```python
PLUGIN = {
    "name": "leads_forwarder",
    "monitor_patterns": [
        {"group": "广州租房群", "user": "", "message": ".*留资.*"},
        {"group": "", "user": "管理员", "message": ".*"},
        {"group": ".*群", "user": "", "message": "^\\d+/.*=.*$"},
        # 关键词只需匹配正则（word boundary）
        {"group": "", "user": "", "message": "(^|\\s)(1|0|好的|ok)(\\s|$)"},
    ],
}
```

**匹配语义**：
- `group` 为空 → 匹配所有群/联系人；非空 → 字符串 `in` 匹配（大小写不敏感时可用 `.*`）。
- `user` 为空 → 匹配所有发送者；非空 → 字符串 `in` 匹配。
- `message` 为空 → 匹配所有消息；非空 → `regex.search(message)`。

**注意**：只有**至少注册了一条** `monitor_patterns` 的插件才可能被监听引擎
推送消息。若插件只想在收到消息时处理**但无需过滤**，请注册一条全匹配模式：
`{"group": "", "user": "", "message": ""}`（空 message 表示匹配所有）。

### 2.2 监听链路（v0.4.0 重写）

```
微信收到新消息 → 播放提示音
        │ pycaw 检测到 Weixin.exe 音频峰值
        ▼
触发聊天列表扫描：从头到尾遍历
        │ 大红点+数字  = 有未读    → 打开会话，跳到"XX条新消息"，OCR 收集
        │ 小红点+无数字 = 消息免打扰 → 跳过
        ▼
对每条消息做插件模式匹配（群+用户+消息正则）
        ▼
触发 on_message(ctx)，收集回复 → engine.send_text 发回
        │
        ▼（某群持续有消息时）
持续监听：焦点在浏览器，微信窗口后台开着；OCR 持续读取；按活跃度切换会话
```

**前提**：`config.json` 中 `monitor.enabled: true` 且 `monitor.continuous_monitor`（默认开）。

- `monitor.monitor_all: true`（默认）：监控所有**非免打扰**会话。
- `monitor.listen_contacts`: 指定监听集合（`monitor_all=false` 时生效）。

### 2.3 `requires`（第三方依赖）

声明第三方包，加载时自动检测，缺失则标记加载失败并提示：

```python
PLUGIN = {
    "name": "excel_plugin",
    "requires": ["openpyxl"],   # 缺失 → 列表显示"缺少依赖: openpyxl"
}
```

- **已内置在 exe 中**（无 Python 环境也可直接 import）：`Pillow`、`openpyxl`、
  `xlsxwriter`、`requests`、`rapidocr_onnxruntime`、`onnxruntime`、`cv2` 等。
- **未内置的可选依赖**（如 `playwright`，含浏览器二进制服体积过大）：**不要**
  放进 `requires`（会阻塞加载）。插件内部自行 `try: import ... except ImportError: 降级`。
  参见 `leads_forwarder/renderer.py`：playwright 不可用时降级 Pillow。

---

## 3. 事件处理

对 `events` 里的每个事件 `xxx`，实现 `async def on_xxx(ctx)`。

### 3.1 `on_message(ctx)` 上下文结构

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `event_type` | str | 固定 `"message"` |
| `message_type` | str | 微信场景为 `"group"`（群名含「群」）或 `"private"` |
| `contact` | str | 来源会话名（微信联系人/群名） |
| `message` | str | 消息文本 |
| `raw_message` | str | 原始消息文本 |
| `sender` | dict | `{"name": 发送者名}`（OCR 尽力归因，可能为空） |
| `engine` | object | UIEngine 实例，可调用发消息等 |
| `matched_plugins` | list[str] | 触发本事件的插件名列表 |

### 3.2 返回值（回复）

返回 **OneBot v11 消息段列表**：

```python
[{"type": "text", "data": {"text": "回复内容"}}]
```

返回 `[]` 表示不回复。多个插件可同时回复同一消息。也可用 `engine.send_text`
主动发消息（返回后无需再返回段）。

---

## 4. OneBot 自定义 action

对 `actions` 里的每个 action `xxx`，实现
`async def action_xxx(engine, **params)`：

```python
async def action_my_query(engine, **params):
    return {"ok": True, "received": params}
```

调用方式（OneBot v11 标准）：

```bash
# HTTP
curl -X POST http://127.0.0.1:3000/my_query \
  -H "Content-Type: application/json" \
  -d '{"action":"my_query","params":{"key":"value"}}'

# WebSocket: 发送 {"action":"my_query","params":{...}}
```

`engine`（UIEngine）可用能力：

| 方法 | 说明 |
| --- | --- |
| `await engine.send_text(contact, text)` | 发文本 |
| `await engine.send_image_file(contact, path)` | 发图片文件 |
| `await engine.send_image_bytes(contact, data)` | 发图片字节 |
| `await engine.send_at_message(contact, at_name, text)` | 群内 @ 发送 |
| `await engine.get_chat_list()` | 获取聊天列表 |
| `await engine.get_status()` | 引擎状态 |
| `engine.adapter` | 微信适配器（可 `_get_screenshot()` 等） |

---

## 5. 插件设置（后台可配置）

在 `PLUGIN["settings"]` 声明设置项，MagpieBridge 控制台「插件管理」→「设置」
会**自动生成表单**，无需写界面代码。保存后立即热生效（无需重载）。

```python
PLUGIN = {
    "name": "my_plugin",
    "settings": [
        {"key": "groups",  "label": "目标群（每行一个）", "type": "textarea",
         "default": "",    "description": "线索转发到的微信群"},
        {"key": "reply_timeout", "label": "回复超时（分钟）", "type": "number", "default": 5},
        {"key": "verbose", "label": "详细日志", "type": "boolean", "default": False},
        {"key": "send_mode", "label": "发送格式", "type": "select",
         "options": ["text", "image"], "default": "text"},
    ],
}
```

### 5.1 设置项字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `key` | ✅ | 设置键名（存储/读取用） |
| `label` | ✅ | 表单显示名称 |
| `type` | - | `text`(默认)/`textarea`/`number`/`boolean`/`select` |
| `default` | - | 默认值 |
| `description` | - | 表单下方说明 |
| `options` | - | `select` 的候选项（list[str]） |

### 5.2 运行时读取

加载时设置被注入为模块级全局 `PLUGIN_SETTINGS`（dict）。保存后**立即热更新**：

```python
PLUGIN_SETTINGS = {}          # 模块顶部声明默认值，避免 NameError

def _my_setting():
    settings = globals().get("PLUGIN_SETTINGS") or {}
    return settings.get("reply_timeout", 5)
```

### 5.3 存储与 API

- 存储位置：`Plugins/config/{插件名}.json`
- `GET  /api/plugins/{name}/settings` → `{schema, values}`
- `POST /api/plugins/{name}/settings` → 部分更新（只改传入的键）
- 编程式保存：`engine.plugin_manager.save_plugin_settings(name, values)`

---

## 6. 完整示例（@ 回复 + 图片 + 后台任务）

```python
# -*- coding: utf-8 -*-
# Plugins/reporter.py

PLUGIN = {
    "name": "reporter",
    "version": "1.0.0",
    "description": "收到'日报'自动发日报；收到'天气图'发图片",
    "author": "你",
    "events": ["message"],
    "actions": ["report_now"],
    "monitor_patterns": [{"group": "", "user": "", "message": ""}],
    "settings": [{"key": "tag", "label": "标签", "type": "text", "default": ""}],
}

async def on_message(ctx):
    msg = (ctx.get("message") or "").strip()
    contact = ctx.get("contact", "")
    engine = ctx.get("engine")

    if msg == "日报":
        return [{"type": "text", "data": {"text": "今日日报：\n- 任务完成 3 项"}}]
    if msg == "天气图" and engine:
        await engine.send_image_file(contact, r"C:\temp\weather.png")
        return []
    return []

async def action_report_now(engine, **params):
    return {"ok": True, "report": "今日日报：无异常"}
```

**后台定时任务**（如超时监控）：插件没有专门的启动钩子，用惰性启动即可——在
首次 `on_message`/`action_*` 被调用时用模块级标志 + `create_task` 启动一次：

```python
_lazy_task_started = False

def _ensure_task():
    global _lazy_task_started
    if _lazy_task_started:
        return
    _lazy_task_started = True
    asyncio.get_running_loop().create_task(my_background_loop())

async def on_message(ctx):
    _ensure_task()
    ...
```

---

## 7. 插件管理 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/plugins` | 列出全部插件（含启用状态/加载错误） |
| POST | `/api/plugins/reload` | 热重载 `Plugins/` 目录 |
| POST | `/api/plugins/{name}/enable` | 启用插件 |
| POST | `/api/plugins/{name}/disable` | 禁用插件 |
| GET | `/api/plugins/{name}/settings` | 读取设置 schema + 当前值 |
| POST | `/api/plugins/{name}/settings` | 保存设置（部分更新） |

```bash
curl http://127.0.0.1:8080/api/plugins
curl -X POST http://127.0.0.1:8080/api/plugins/reload
curl -X POST http://127.0.0.1:8080/api/plugins/echo/disable
```

---

## 8. 内置 OneBot action（不写插件即可用）

| action | 参数 | 说明 |
| --- | --- | --- |
| `send_private_msg` | `user_id`, `message` | 给联系人发消息 |
| `send_group_msg` | `group_id`, `message` | 给群发消息 |
| `send_msg` | `message_type`, `user_id`/`group_id`, `message` | 通用发送 |
| `get_status` | - | 引擎状态 |
| `get_chat_list` | - | 微信聊天列表 |

`message` 支持文本字符串或 OneBot 消息段数组（仅取 `text` 段）。

---

## 9. 常见问题

- **插件加载失败？** `GET /api/plugins` 的 `error` 字段给出原因。常见：缺 `PLUGIN`/`name`、语法错误、`_` 开头。
- **插件报错会影响主程序吗？** 不会。每个插件在独立 try/except 中执行。
- **改完要重启吗？** 不需要，`POST /api/plugins/reload`。
- **能访问截图/OCR 吗？** 可以，`ctx["engine"].adapter`（如 `adapter._get_screenshot()`）。
- **监控没触发？** 检查 `monitor.enabled`、确认目标群未被设为免打扰（免打扰小红点会被跳过）、
  已注册 `monitor_patterns`。
- **调试**：日志 `logs/magpie.log`；操作留档 `grep "[AUDIT]" logs/magpie.log`；
  界面 TUI 左下运行日志、右侧操作日志（AUDIT）、底部操作队列。
- **调试时不想被抢焦点/抢鼠标**：在管理后台“控制台”或按全局快捷键 `Ctrl+Alt+S` 开启/关闭「模拟网页浏览」
  （**默认关闭**）。开启后无任务时才会丢焦点看网页。关闭时不干扰你操作电脑。
  暂停只影响空闲丢焦点浏览，**不影响**插件收消息/回复（插件仍正常工作）。
