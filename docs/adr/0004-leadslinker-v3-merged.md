# ADR-0004: LeadsLinker V3 并入主仓,作为官方插件范例

日期:2026-09-07
状态:已接受

## 背景

LeadsLinkerV2 原是独立工作区(浏览器扩展 + OneBot 插件),靠手工拷贝
同步到宿主 `Plugins/` 目录,dist 打包副本长期落后于源码,联调脚本里的
路径也早已失效。

## 决策

- 主仓新增 `extension/`(Chrome MV3 扩展)与 `plugins/leads_forwarder/`
  (OneBot 插件,宿主运行时直接加载,不再有"源目录→部署目录"两份拷贝);
- 套件版本统一 **LeadsLinker V3**(扩展 manifest 3.0.0 + 插件 3.0.0),
  与宿主 MagpieBridge V5.0 配套;
- 插件内复制的 target_guard 匹配逻辑改为直接 import 宿主 `magpie.core.target_guard`;
- 旧 LeadsLinkerV2 目录整体移入 `_archive/`,不再维护。

## 后果

- 单一事实源:改插件即改部署,build.bat 打包时自动带上最新插件。
- 插件因此成为"如何给鹊桥写插件"的官方范例(事件/动作/设置 schema/
  发送三态消费/CSV 台账全用上了)。
