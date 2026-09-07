# 鹊桥 MagpieBridge V5.0 — 完整使用与开发文档

> 微信消息推送 & OneBot v11 & 插件生态 —— 基于 UI 自动化(OCR + 拟人操作)的 7x24 桌面自动化宿主。
> 配套官方插件套件 **LeadsLinker V3**:抖音来客线索 → 微信群 @收单员。

---

## 1. 它是什么

鹊桥(MagpieBridge)是一个**宿主进程**,一边连着微信桌面客户端(通过屏幕 OCR + 拟人鼠标键盘),一边对上层暴露标准协议:

```
浏览器扩展/机器人框架/脚本 ──OneBot v11(HTTP :3000 / WS :3001)──┐
Web 管理后台(:8080)──────────────────────────────────────┤→ 鹊桥引擎 → 微信 4.1.13
插件(python, plugins/ 目录)──────────────────────────────┘
```

- **发送三态**:每个发送动作都给出 `sent / failed / uncertain`,失败绝不谎报成功,`uncertain` 禁止盲目重发。
- **防发错人链条**:目标名归一化 → 守卫匹配(禁子串、拒同名编号群)→ 输入前复核标题 → Enter 前最后一道闸 → 发送后双条件验证 + 绿色气泡复核。
- **插件系统**:放个 Python 文件/文件夹进 `plugins/` 即接入消息事件、自定义 action、后台设置表单。
- **7x24 设计**:RDP 断开自动挂回控制台、息屏照常工作、操作队列串行化 + 双超时、日志按天滚动。

## 2. 快速开始

1. 安装 Python 3.11+(或直接用 [Releases](../../releases) 的打包 exe);
2. `pip install -r requirements.txt`;
3. 复制 `config.example.json` 为 `config.json`,按需修改(默认即可跑);
4. 登录微信桌面版,然后 `python main.py`(需要管理员权限,UAC 弹一次);
5. 打开 `http://127.0.0.1:8080` 管理后台。

### 全局快捷键

| 快捷键 | 功能 |
|---|---|
| `Ctrl+Alt+O` | 息屏/亮屏切换(系统级息屏,后台照常运行) |
| `ESC` | 息屏时唤醒(平时无作用) |
| `Ctrl+Alt+E` | 开始挂起(挂回本机控制台,断开远程桌面) |
| `Ctrl+Alt+Y` | 让出鼠标 30 秒(人工操作时不被打架) |
| `Ctrl+Alt+S` | 开/关空闲网页浏览 |

## 3. 数据与存储(ADR-0002)

| 数据 | 存放 | 谁写 |
|---|---|---|
| 收发消息流水 | `data/magpie.db`(SQLite WAL) | 程序 |
| 主配置 | `config.json`(人手可编辑) | 人 + 后台 |
| 插件设置 | `plugins/config/<名>.json` | 人 + 后台 |
| 导出/留档 | `data/exports/*.csv`(UTF-8 BOM,Excel 直开) | 后台一键导出 |
| LeadsLinker 台账 | `~/LeadsLinker/leads/*.csv` | 插件 |

用户查看数据:Web 后台「消息」页,或 `POST /api/export/csv` 导出 CSV。**不要直接编辑 .db**;WAL 模式保证后台翻看时程序照常写入。

旧版升级:启动时自动迁移旧 `messages.db` 与 `~/MsgPushWechat/data/messages/*.csv`,原文件保留改名 `.migrated`。

## 4. OneBot v11 API(:3000 / :3001)

| 动作 | 说明 |
|---|---|
| `POST /send_group_msg` | `{"group_id": 群ID或名称, "message": "文本"}` |
| `POST /send_private_msg` | 同上,user_id |
| `POST /send_msg` | message_type=group/private |
| `POST /{插件action}` | 插件自定义 action 直通(如 `send_leads`) |
| `GET /status` | 引擎状态 |

> 群名可直接当 `group_id` 用(字符串),引擎走搜索+守卫定位会话。
> `access_token` 配置后需 `Authorization: Bearer <token>`。

## 5. 管理后台 API(:8080,仅限本机)

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/status` | 引擎 + 监控状态 |
| POST | `/api/send/text` / `/api/send/image` / `/api/send/at` | 发送 |
| GET/POST | `/api/config` | 配置读写(授权码打码) |
| GET | `/api/messages`、`/api/messages/stats`、`/api/messages/contacts` | 消息查询 |
| POST | `/api/messages/delete`、`/api/messages/clear`、`/api/export/csv` | 删除/导出 |
| POST | `/api/screen` | `{"action":"off/on/toggle/status"}` 息屏控制 |
| GET/POST | `/api/monitor/*` | 监听管理 |
| GET/POST | `/api/plugins/*` | 插件管理/设置 |
| GET | `/api/audit/log` | 操作审计 |

## 6. 插件开发

最小插件(放 `plugins/my_plugin.py`,后台点「重载插件」即生效):

```python
PLUGIN = {
    "name": "my_plugin",
    "version": "1.0.0",
    "events": ["message"],                      # 订阅消息事件
    "monitor_patterns": [                       # 可选:群/用户/正则过滤
        {"group": "我的群", "user": "", "message": ".*关键词.*"},
    ],
    "actions": ["ping"],                        # OneBot 自定义 action
    "settings": [                               # 后台设置表单
        {"key": "greeting", "type": "text", "default": "你好"},
    ],
}

async def on_message(ctx):
    # ctx: contact / message / sender / engine / matched_plugins
    if "关键词" in ctx["message"]:
        return [{"type": "text", "data": {"text": "收到!"}}]
    return []

def action_ping(engine, **params):
    return {"ok": True, "params": params}
```

约定:`on_<事件>` 收事件(可 async,返回消息段列表即自动回复);
`action_<名>` 定义 OneBot action(第一个参数注入 engine);
`PLUGIN_SETTINGS` 由宿主注入持久化设置;设置文件 `plugins/config/<名>.json` 人手可改,保存即热生效。
官方范例:`plugins/leads_forwarder/`(LeadsLinker V3,用全了事件/动作/设置/三态消费)。

## 7. 息屏与 7x24 运维(ADR-0003)

- **息屏 = 系统级关背光**(SC_MONITORPOWER):显示器仍在线、分辨率不变 → 微信窗口不重排、截图/OCR/合成输入全部照常。**不是**黑色遮罩窗口(z-order 会干扰自动化)。
- 保黑看门狗:6 秒周期重新压黑,防 RDP 接入/杂散输入唤醒;
- 物理输入吞没:息屏期间吞掉人类键鼠(ESC 除外),带 INJECTED 标记的合成输入放行 → 机器完全交给宿主,又不会误碰;
- RDP 断开自愈:会话进入 Disconnected/锁屏时自动 `tscon <sid> /dest:console` 挂回控制台(需管理员,exe 启动时自动提权);
- 独立 CLI:`python tools/screen_off.py off|on|status|toggle`(计划任务可调)。

**为什么显示器不能拔线/断电**:Windows 视为显示拓扑变化 → 分辨率重排 → 微信(Qt)窗口错位甚至锁死。息屏(关背光)是唯一安全方案。

## 8. LeadsLinker V3(官方插件套件)

抖音来客(life.douyin.com)线索 → 鹊桥 → 微信群 @收单员:

- `plugins/leads_forwarder/`:OneBot 插件。接收线索 → 月度 CSV 台账 → 时段排班轮转选群选人 → @发送 → 回复 `1/0/好的/没空` 受理/拒收 → 超时转下一人、阻塞时段暂存补发;`/ll 统计` 群聊命令;AI 调试通道(`debug_*` action)。
- `extension/`:Chrome MV3 扩展。页面线索检测(手机号/微信号/二维码)、转发队列(指数退避永不放弃)、与插件经 OneBot HTTP 通信。
- 群名匹配直接委托宿主 `magpie.core.target_guard`(单一事实源)。
- 部署清单见 `docs/leadslinker-deploy.md`。

## 9. 测试与构建

```bash
pip install -r requirements.txt pytest pytest-asyncio
python -m pytest tests/ -q          # 40 个单元测试(守卫/存储/配置/插件/OneBot/息屏)

build.bat                            # 需要 Python + Node(npm);产出 ..\构建\MagpieBridge-V5.0.0\
```

OCR 模型:`models/` 目录(仓库不内置)存在时打包进 exe;否则用 rapidocr 自带模型。

## 10. 架构决策记录(ADR)

- [ADR-0001 更名鹊桥 MagpieBridge](adr/0001-rename-to-magpiebridge.md)
- [ADR-0002 数据层:SQLite WAL + JSON + CSV 导出](adr/0002-sqlite-wal-unified-store.md)
- [ADR-0003 息屏方案:系统级息屏 + 保黑 + 输入吞没](adr/0003-system-level-screen-off.md)
- [ADR-0004 LeadsLinker V3 并入主仓](adr/0004-leadslinker-v3-merged.md)
- [ADR-0005 重构策略:保留核心,修 bug + 去重 + 清死代码](adr/0005-refactor-strategy.md)

术语表见 [CONTEXT.md](../CONTEXT.md);历史调研文档(`BENCHMARK_ANALYSIS` / `anti_detection` / `REVIEW-发送链路`)保留在 `docs/` 供考古。

## 11. 故障排查

| 症状 | 处置 |
|---|---|
| 发送失败"微信窗口未能置前" | RDP 已断开/桌面无前台。以管理员运行(自动 tscon 挂回);或在远程桌面里点一下任意处恢复前台再试 |
| 发送失败"找不到联系人" | 群名列不唯一/被 OCR 截断;守卫宁可拒发也不发错,核对 config 群名 |
| 微信界面卡死 | 旧版对隐藏窗口 PrintWindow 拖死渲染管线的遗留伤害,重启微信;V5 已用屏幕 BitBlt 规避 |
| 息屏后无法操作 | 这是特性(物理输入被吞)。按 `ESC` 或 `Ctrl+Alt+O` 唤醒 |
| 邮件告警未收到 | 后台「设置→邮件提醒」用「发送测试邮件」验证;授权码是 QQ 邮箱授权码非登录密码 |

## 12. 许可证

MIT License —— 自用项目,学习研究探讨用。
