# ADR-0001: 产品更名「鹊桥 MagpieBridge」,版本跳至 V5.0

日期:2026-09-07
状态:已接受

## 背景

原产品名 `MsgPushWechat` 只描述了"推送微信消息"这一半功能;实际产品是
"桌面 UI 自动化宿主 + OneBot v11 + 插件生态 + 浏览器扩展协同"的完整平台,
且与官方插件套件 LeadsLinker 是宿主/插件关系,需要一个家族化的名字。

## 决策

- 产品名:**鹊桥(MagpieBridge)** —— 连接两侧的信使之桥(浏览器 ↔ OneBot ↔ 微信)。
- Python 包名:`magpie`;仓库名 `MagpieBridge`;可执行文件 `MagpieBridge.exe`。
- 版本直接跳 **5.0.0**:与旧 0.4.x 线彻底切断;插件套件 LeadsLinker 同步升 V3。
- 本地数据目录 `~/MagpieBridge/`(旧 `~/MsgPushWechat/` 自动迁移)。

## 后果

- 旧名只存在于归档与 git 历史中;所有代码、日志、窗口标题、单实例互斥量名同步更换。
- 兼容:config.json 文件名与字段不变,旧配置可直接使用。
