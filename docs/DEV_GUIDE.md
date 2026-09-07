# MagpieBridge 开发文档

> 版本：V0.3.4 | 更新日期：2026-08-30

---

## 1. 项目概述

MagpieBridge 是一个基于 Windows UI Automation 的微信消息推送工具，通过模拟用户操作来收发消息。

### 核心特性
- **OneBot v11 协议**：支持 HTTP/WebSocket 接口
- **智能监控**：声音触发 + OCR 识别未读消息
- **插件系统**：支持自定义插件扩展功能
- **反检测**：模拟人类操作（贝塞尔曲线、帕金森抖动、随机延迟）
- **前端管理**：Vue 3 + Element Plus 管理界面

---

## 2. 架构设计

```
┌─────────────────────────────────────────────────┐
│                  Web 管理界面                      │
│         (Vue 3 + Element Plus)                   │
└──────────────────────┬──────────────────────────┘
                       │ REST API
┌──────────────────────▼──────────────────────────┐
│              MagpieBridge 主进程                 │
│                                                  │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────┐ │
│  │ OneBot v11  │  │   消息日志    │  │ 状态监控 │ │
│  │ 适配层      │  │   审计模块    │  │ 模块    │ │
│  │(HTTP+WS)   │  │              │  │         │ │
│  └──────┬──────┘  └──────────────┘  └─────────┘ │
│         │                                        │
│  ┌──────▼──────────────────────────────────────┐ │
│  │         UI 自动化引擎                        │ │
│  │  • 版本适配层                                │ │
│  │  • 鼠标轨迹模拟（贝塞尔曲线）                 │ │
│  │  • 帕金森抖动                                │ │
│  │  • 随机延迟                                  │ │
│  │  • OCR 文字识别                              │ │
│  │  • 剪贴板操作                                │ │
│  └─────────────────────────────────────────────┘ │
│                                                  │
│  ┌─────────────────────────────────────────────┐ │
│  │         智能监控引擎                         │ │
│  │  • 声音触发（pycaw）                         │ │
│  │  • 定期轮询                                  │ │
│  │  • 插件模式匹配                              │ │
│  │  • CSV 消息存储                              │ │
│  └─────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

---

## 3. 模块说明

### 3.1 核心模块 (`magpie/core/`)

| 模块 | 功能 |
|------|------|
| `config.py` | 配置管理（JSON 配置文件） |
| `window_manager.py` | 窗口定位、置顶、遮挡检测 |
| `human_simulator.py` | 贝塞尔曲线鼠标轨迹、帕金森抖动、随机延迟 |
| `clipboard.py` | 剪贴板文字/图片操作（ctypes Win32 API） |
| `ocr.py` | OCR 文字识别（rapidocr-onnxruntime） |
| `wechat_adapter.py` | 微信适配器抽象接口 |
| `wechat_adapter_4x.py` | 微信 4.x 适配实现（OCR + 坐标定位） |
| `ui_engine.py` | 高层 UI 自动化引擎 |

### 3.2 监控模块 (`magpie/monitor/`)

| 模块 | 功能 |
|------|------|
| `listener.py` | 智能监听器（声音触发 + 角标扫描 + 活跃群持续 OCR） |
| `sound_monitor.py` | 声音检测（pycaw 读取 Weixin 峰值表） |
| `logger.py` | SQLite 消息日志/审计 |
| `status.py` | 状态监控 |

### 3.3 TUI 模块 (`magpie/tui/`)

| 模块 | 功能 |
|------|------|
| `app.py` | Textual 终端界面（状态/日志/操作日志/操作队列） |
| `bus.py` | 进程内事件总线（状态发布、操作队列、日志缓冲） |
| `log_handler.py` | 把日志镜像到 TUI 日志面板 |

### 3.3 OneBot 模块 (`magpie/onebot/`)

| 模块 | 功能 |
|------|------|
| `models.py` | OneBot v11 数据模型 |
| `v11_http.py` | OneBot HTTP 服务 |
| `v11_ws.py` | OneBot WebSocket 服务 |

### 3.4 插件系统 (`magpie/plugin/`)

| 模块 | 功能 |
|------|------|
| `manager.py` | 插件管理器（加载、卸载、设置） |

---

## 4. 配置文件

### 4.1 config.json 结构

```json
{
  "onebot": {
    "http_host": "127.0.0.1",
    "http_port": 3000,
    "ws_host": "127.0.0.1",
    "ws_port": 3001,
    "access_token": ""
  },
  "wechat": {
    "window_position_x": 0,
    "window_position_y": 0,
    "window_width": 900,
    "window_height": 600,
    "chat_list_x1": 70,
    "chat_list_y1": 70,
    "chat_list_x2": 335,
    "chat_list_y2": 580,
    "input_box_x": 580,
    "input_box_y": 515,
    "send_button_x": 835,
    "send_button_y": 545
  },
  "monitor": {
    "enabled": true,
    "monitor_all": true,
    "poll_interval_sec": 3,
    "listen_contacts": []
  },
  "admin": {
    "enabled": false,
    "admin_contacts": []
  }
}
```

### 4.2 配置说明

| 配置项 | 说明 |
|--------|------|
| `wechat.chat_list_*` | 聊天列表检测区域坐标 |
| `wechat.input_box_*` | 输入框位置坐标 |
| `wechat.send_button_*` | 发送按钮位置坐标 |
| `monitor.monitor_all` | 是否监控所有非免打扰聊天 |
| `monitor.listen_contacts` | 指定监听的联系人/群列表 |

---

## 5. API 接口

### 5.1 OneBot v11 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/send_private_msg` | 发送私聊消息 |
| POST | `/send_group_msg` | 发送群消息 |
| GET | `/get_status` | 获取状态 |
| GET | `/get_chat_list` | 获取聊天列表 |

### 5.2 管理接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/status` | 系统状态 |
| GET | `/api/config` | 获取配置 |
| POST | `/api/config` | 保存配置 |
| POST | `/api/send/text` | 发送文本消息 |
| POST | `/api/send/image` | 发送图片消息 |
| GET | `/api/chat/list` | 获取聊天列表 |

### 5.3 监控接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/monitor/list` | 查看监控状态 |
| POST | `/api/monitor/add` | 添加监听会话 |
| POST | `/api/monitor/remove` | 移除监听会话 |
| POST | `/api/monitor/start` | 启动监听 |
| POST | `/api/monitor/stop` | 停止监听 |

### 5.4 插件接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/plugins` | 列出所有插件 |
| POST | `/api/plugins/reload` | 热重载插件 |
| POST | `/api/plugins/{name}/enable` | 启用插件 |
| POST | `/api/plugins/{name}/disable` | 禁用插件 |

---

## 6. 插件开发

### 6.1 插件目录结构

```
MagpieBridge.exe
└── Plugins/
    └── my_plugin/
        ├── __init__.py
        └── renderer.py
```

### 6.2 插件元信息

```python
PLUGIN = {
    "name": "my_plugin",
    "version": "1.0.0",
    "description": "插件描述",
    "author": "作者",
    "events": ["message"],
    "actions": ["my_action"],
    "monitor_patterns": [
        {"group": "群名", "user": "", "message": "正则表达式"},
    ],
    "settings": [
        {"key": "setting1", "label": "设置1", "type": "text", "default": ""},
    ],
}
```

### 6.3 消息事件处理

```python
async def on_message(ctx):
    msg = ctx.get("message", "")
    contact = ctx.get("contact", "")
    engine = ctx.get("engine")
    
    # 处理消息...
    
    return [{"type": "text", "data": {"text": "回复内容"}}]
```

### 6.4 模式匹配

`monitor_patterns` 用于声明插件监听的消息模式：
- `group`: 群名（空=所有）
- `user`: 发送者（空=所有）
- `message`: 消息内容正则（空=所有）

---

## 7. 构建打包

### 7.1 开发环境

```bash
# 安装依赖
pip install -r requirements.txt

# 安装前端依赖
cd web && npm install

# 构建前端
npm run build
```

### 7.2 打包 exe

```bash
# Windows
build.bat

# 或手动
python -m PyInstaller MagpieBridge.spec --clean --noconfirm
```

### 7.3 输出目录

```
E:\平日资料\构建\MagpieBridge-V0.3.4\
├── MagpieBridge.exe
├── config.json
├── data/
├── logs/
└── plugins/
```

---

## 8. 声音检测

使用 `pycaw` 库监听 Windows 音频会话，检测微信（Weixin.exe）的音频峰值。

### 工作流程

1. 启动时初始化 pycaw，查找微信音频会话
2. 每 0.3 秒检查微信音频峰值
3. 峰值 > 0.01 时触发扫描
4. 扫描所有非免打扰聊天
5. OCR 识别未读消息
6. 插件模式匹配

### 依赖

```
pycaw>=20230407
```

---

## 9. 反检测策略

### 9.1 鼠标轨迹
- 贝塞尔曲线路径
- 帕金森抖动（±2px）
- 随机速度

### 9.2 操作延迟
- 操作间随机延迟（200-1500ms）
- 高斯分布间隔
- 人类反应延迟

### 9.3 浏览器模拟

- 无任务消息时自动丢焦点，打开 1-2 个网页（固定页数，去重不重复开）
- 工作时间模拟人类上下滚动（PgUp/PgDn，不抢夺鼠标焦点）
- 每 30~60 分钟自动 F5 刷新
- **全局快捷键** `Ctrl+Alt+S`（可在 `config.json` 的 `monitor.browse_pause_hotkey` 修改）
  一键暂停/恢复网页查看 —— 调试时避免软件抢焦点/抢鼠标，TUI 顶部有「网页浏览: 暂停/进行」状态

### 9.4 聊天列表搜索
- 滚动到顶部后搜索
- 模拟误点和修正
- 随机滚动方向

---

## 10. 常见问题

### Q: 插件加载失败？
检查 `Plugins/` 目录下的插件文件是否有语法错误。

### Q: 微信窗口找不到？
确保微信已启动并登录，窗口未最小化到托盘。

### Q: OCR 识别不准确？
检查 `wechat.chat_list_*` 坐标是否正确，可以通过截图验证。

### Q: 声音检测不工作？
检查微信是否正在播放声音，pycaw 需要微信有音频会话。

---

## 11. 版本历史

| 版本 | 日期 | 更新内容 |
|------|------|----------|
| V0.4.0 | 2026-08-30 | 监听引擎重构、TUI 界面、角标分类（大红点/免打扰小红点）、活跃群持续 OCR |
| V0.3.4 | 2026-08-30 | 声音检测、智能监控、插件模式匹配 |
| V0.3.3 | 2026-08-30 | OCR 聊天列表识别、图片发送修复 |
| V0.3.2 | 2026-08-30 | 前端中英双语、配置保存功能 |
| V0.3.1 | 2026-08-30 | 基础功能实现 |
