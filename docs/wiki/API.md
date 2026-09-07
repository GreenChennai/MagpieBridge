# 接口文档(OneBot v11 + 管理后台)

## OneBot v11

- HTTP:`http://127.0.0.1:3000`(`POST /{action}`,body `{"action":..., "params":..., "echo":...}`)
- WebSocket:`ws://127.0.0.1:3001`
- 鉴权:配置 `onebot.access_token` 后,HTTP 用 `Authorization: Bearer <token>`,WS 用同 header 或 `?access_token=`

### 内置 action

| action | 参数 | 说明 |
|---|---|---|
| `send_group_msg` | `group_id`, `message` | message 支持字符串或消息段数组(text 段拼接) |
| `send_private_msg` | `user_id`, `message` | 同上 |
| `send_msg` | `message_type`, `user_id`, `group_id`, `message` | 二选一 |
| `get_status` | — | 引擎状态 |
| `get_chat_list` | — | 可见会话列表 |
| `get_group_list` / `get_friend_list` | — | UI 自动化拿不到,恒返回 `[]` |

> **群名可直接当 `group_id`/`user_id` 传**(字符串)——引擎走「搜索 + 守卫」定位会话。
>
> **失败如实上报**:发送失败返回 `retcode: 200`(而非伪造 message_id);
> 超时类失败在引擎层标 `uncertain`,**禁止盲目重发**(消息可能稍后已发出)。

### 插件 action

未注册的 action 落到插件系统。示例(LeadsLinker):

```bash
curl -X POST http://127.0.0.1:3000/send_leads -H "Content-Type: application/json" \
  -d '{"params": {"name":"张三","phone":"13800000000","wechat":"wx_abc"}}'
```

`debug_` 前缀的 action(调试通道轮询)不进审计日志,防止刷屏。

## 管理后台 API(:8080,仅限本机回环)

安全基线:全部 `/api` 只接受 `127.0.0.1 / ::1` 来源(外部网段 403);前端同源部署,无 CORS。

### 状态与消息

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/status` | 引擎 + 监控状态 |
| GET | `/api/messages?limit=&contact=` | 消息流水(收发同源) |
| GET | `/api/messages/stats` | 总数/成功/失败/会话数 |
| GET | `/api/messages/contacts` | 按会话分组 |
| POST | `/api/messages/delete` | `{"ids":[..]}` 或 `{"contact":".."}` |
| POST | `/api/messages/clear` | `{"contact":null}` 清全部 |
| POST | `/api/export/csv` | `?contact=` 缺省按会话全导,UTF-8 BOM → `data/exports/` |

### 发送

| 方法 | 路径 | body |
|---|---|---|
| POST | `/api/send/text` | `{"contact":"群名","message":"..."}` |
| POST | `/api/send/image` | `{"contact":"..","file_path":".."}` |
| POST | `/api/send/at` | `{"contact":"..","at_name":"张三","message":".."}` |

### 配置与告警

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/api/config` | 授权码打码返回;POST 时留空/打码值 = 不覆盖 |
| POST | `/api/alert/test` | 发测试邮件验证 SMTP |
| POST | `/api/admin/command` | `{"command":"/text 内容->目标"}` |

### 监听 / 插件 / 息屏 / 审计

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/monitor/list`、`/api/monitor/messages` | 监听状态/内存消息 |
| POST | `/api/monitor/add|remove|start|stop` | `{"contact":".."}` |
| GET/POST | `/api/plugins`、`/api/plugins/reload` | 列表/热重载 |
| GET/POST | `/api/plugins/{name}/settings` | schema + 值 / 保存(热生效) |
| POST | `/api/screen` | `{"action":"off/on/toggle/status"}` |
| GET | `/api/audit/log`、`/api/browse` | 审计 / 浏览开关 |
