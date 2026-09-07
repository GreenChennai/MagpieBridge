# 鹊桥 MagpieBridge

> 微信消息推送 & OneBot v11 & 插件生态 —— 基于 UI 自动化的 7x24 桌面自动化宿主。
> 官方插件套件 **LeadsLinker V3**:抖音来客线索 → 微信群 @收单员。

**仓库首页保持极简(自用项目,学习研究探讨用)。全部文档在本 Wiki 与 `docs/` 目录。**

## 页面导航

- [[Home]] — 本页(概览 + 快速开始)
- [[API]] — OneBot v11 与管理后台接口
- [[Plugin-Dev]] — 插件开发指南
- [[Ops-Screen-Off]] — 息屏与 7x24 运维
- [[LeadsLinker-V3]] — 官方插件套件
- [[Architecture-ADR]] — 架构与决策记录

## 它是什么

```
浏览器扩展/机器人框架/脚本 ──OneBot v11(HTTP :3000 / WS :3001)──┐
Web 管理后台(:8080)──────────────────────────────────────┤→ 鹊桥引擎 → 微信 4.1.13
插件(python, plugins/ 目录)──────────────────────────────┘
```

- **发送三态**:`sent / failed / uncertain`,失败绝不谎报成功,uncertain 禁止盲目重发;
- **防发错人链条**:归一化 → 守卫匹配 → 输入前复核标题 → Enter 前闸 → 发送后双条件验证 + 绿色气泡复核;
- **插件系统**:Python 文件放 `plugins/` 即接入事件/动作/后台设置;
- **7x24**:RDP 断开自愈、系统级息屏照常工作、操作队列串行化、日志按天滚动。

## 快速开始

1. Python 3.11+ → `pip install -r requirements.txt`(或直接用 Releases 的 exe);
2. `cp config.example.json config.json`;
3. 登录微信桌面版 → `python main.py`(自动提权,UAC 弹一次);
4. 打开 `http://127.0.0.1:8080`。

### 快捷键

| 键 | 功能 |
|---|---|
| `Ctrl+Alt+O` | 息屏/亮屏 |
| `ESC` | 息屏时唤醒 |
| `Ctrl+Alt+E` | 开始挂起(挂回控制台) |
| `Ctrl+Alt+Y` | 让出鼠标 30 秒 |
| `Ctrl+Alt+S` | 空闲浏览开关 |

## 数据存放

| 数据 | 位置 |
|---|---|
| 收发消息 | `data/magpie.db`(SQLite WAL) |
| 主配置 | `config.json`(Notepad 可改) |
| 插件设置 | `plugins/config/<名>.json` |
| 导出留档 | `data/exports/*.csv`(Excel 直开) |

详见 [[API]]、[[Plugin-Dev]]、[[Ops-Screen-Off]]、[[LeadsLinker-V3]]。
