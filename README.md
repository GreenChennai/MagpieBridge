# MagpieBridge

> 微信消息推送工具 - 基于 UI Automation 的智能监控系统

[![Version](https://img.shields.io/badge/version-V0.4.14-blue.svg)](https://github.com/your-repo/MagpieBridge)
[![Python](https://img.shields.io/badge/python-3.11+-green.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

---

## 功能特性

- **OneBot v11 协议**：支持 HTTP/WebSocket 接口，兼容主流机器人框架
- **声音触发监听**：微信提示音触发扫描，红点角标分类（大红点+数字=未读，小红点无数字=免打扰跳过）
- **活跃群持续 OCR**：焦点在浏览器，微信窗口后台持续读取，按活跃度切换会话
- **空闲丢焦点浏览**：默认关闭，后台「控制台」或 `Ctrl+Alt+S` 开启后，无任务时固定看 1-2 个网页（检测已开页面避免重复标签、模拟上下滚动、定时 F5）
- **OCR 识别**：使用 rapidocr-onnxruntime 识别微信聊天内容
- **插件系统**：支持 `monitor_patterns`（群+用户+消息正则）、事件、action、后台设置
- **反检测**：模拟人类操作（贝塞尔曲线、帕金森抖动、随机延迟）
- **TUI 终端界面**：顶部状态/左下日志/右下操作日志/底部操作队列
- **Web 管理界面**：Vue 3 + Element Plus 管理界面
- **SMTP 邮件提醒**：发送失败 / 触发风控时自动发邮件提醒人工接管（QQ 邮箱授权码，后台可配置开关）

---

## 快速开始

### 1. 下载安装

从 [Releases](https://github.com/your-repo/MagpieBridge/releases) 下载最新版本，解压到任意目录。

### 2. 配置

编辑 `config.json`：

```json
{
  "onebot": {
    "http_port": 3000,
    "ws_port": 3001
  },
  "wechat": {
    "chat_list_x1": 70,
    "chat_list_y1": 70,
    "chat_list_x2": 335,
    "chat_list_y2": 580
  },
  "monitor": {
    "enabled": true,
    "monitor_all": true
  }
}
```

### 3. 启动

1. 打开微信并登录
2. 运行 `MagpieBridge.exe`
3. 打开浏览器访问 `http://127.0.0.1:8080`

---

## 使用方法

### 发送消息

**通过 Web 界面**：
1. 打开管理界面
2. 输入联系人/群名和消息
3. 点击发送

**通过 OneBot API**：
```bash
# 发送私聊消息
curl -X POST http://127.0.0.1:3000/send_private_msg \
  -H "Content-Type: application/json" \
  -d '{"user_id": 123456, "message": "你好"}'

# 发送群消息
curl -X POST http://127.0.0.1:3000/send_group_msg \
  -H "Content-Type: application/json" \
  -d '{"group_id": 654321, "message": "大家好"}'
```

### 监控消息

**配置监听**：
```json
{
  "monitor": {
    "enabled": true,
    "monitor_all": true
  }
}
```

**通过 API 添加监听**：
```bash
curl -X POST http://127.0.0.1:8080/api/monitor/add \
  -H "Content-Type: application/json" \
  -d '{"contact":"群名"}'
```

---

## 邮件提醒（发送失败 / 风控告警）

当微信消息发送失败（找不到联系人、输入框写入失败、发送按钮不响应、异常等）时，
自动通过 SMTP 发送邮件提醒人工接管。**授权码不写死在代码里**，由使用者在后台自行填写保存。

**配置步骤（QQ 邮箱示例）**：
1. 打开管理后台 `http://127.0.0.1:8080` → 「设置」→「邮件提醒」
2. 开启「启用邮件提醒」开关
3. 填写：
   - SMTP 服务器：`smtp.qq.com`
   - 端口：`465`（勾选 SSL 加密；或 587 走 STARTTLS）
   - 发件邮箱：你的完整 QQ 邮箱（如 `xxx@qq.com`）
   - 授权码：QQ 邮箱 → 设置 → 账号 → 开启 POP3/IMAP/SMTP 服务 → 生成的**授权码**（不是 QQ 登录密码）
   - 收件邮箱：告警邮件发往的邮箱
4. 点击「发送测试邮件」验证配置
5. 保存配置（授权码留空 = 保持不变，不会覆盖已保存的授权码）

**触发时机**：任何发送路径（Web 后台 / OneBot API / 插件转发 / 管理员指令 / 监听自动回复）
失败或异常时都会触发。冷却期内（默认 300 秒）重复失败只发一封，并合并累计次数，防止刷屏。

详细说明见 [docs/EMAIL_ALERT.md](docs/EMAIL_ALERT.md)

---

## 插件开发

### 最小插件

```python
# Plugins/my_plugin.py

PLUGIN = {
    "name": "my_plugin",
    "version": "1.0.0",
    "events": ["message"],
    "monitor_patterns": [
        {"group": "群名", "user": "", "message": ".*关键词.*"},
    ],
}

async def on_message(ctx):
    msg = ctx.get("message", "")
    if "关键词" in msg:
        return [{"type": "text", "data": {"text": "收到"}}]
    return []
```

详细文档请参考 [PLUGIN_DEV.md](docs/PLUGIN_DEV.md)

---

## API 接口

### OneBot v11

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/send_private_msg` | 发送私聊消息 |
| POST | `/send_group_msg` | 发送群消息 |
| GET | `/get_status` | 获取状态 |

### 管理接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/status` | 系统状态 |
| POST | `/api/send/text` | 发送文本消息 |
| POST | `/api/send/image` | 发送图片消息 |
| POST | `/api/send/at` | 发送 @ 消息 |
| GET | `/api/monitor/list` | 监控状态 |
| POST | `/api/monitor/add` | 添加监听 |
| GET | `/api/config` | 获取配置（授权码打码返回） |
| POST | `/api/config` | 保存配置（授权码留空/打码值不覆盖） |
| POST | `/api/alert/test` | 发送测试邮件（验证 SMTP/授权码） |

---

## 配置说明

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `wechat.chat_list_*` | 聊天列表检测区域 | 根据窗口大小 |
| `monitor.enabled` | 启用监控 | false |
| `monitor.monitor_all` | 监控所有聊天 | true |
| `monitor.poll_interval_sec` | 轮询间隔 | 3 |

---

## 开发文档

- [插件开发文档](docs/PLUGIN_DEV.md)
- [开发指南](docs/DEV_GUIDE.md)
- [反检测策略](docs/anti_detection.md)
- [邮件提醒配置](docs/EMAIL_ALERT.md)

---

## 许可证

MIT License
