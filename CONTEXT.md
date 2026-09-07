# CONTEXT — 术语表

本文件只做一件事:定义项目语言。所有文档、代码、日志使用以下词汇时,含义以此为准。

## 核心域

- **鹊桥 MagpieBridge**:本软件产品名。宿主进程,提供微信 UI 自动化、OneBot v11 服务、插件运行时、Web/TUI 控制台。简称"宿主"。
- **宿主 (Host)**:即 MagpieBridge 进程本身。插件与浏览器扩展都挂在宿主上。
- **引擎 (Engine / UIEngine)**:对"微信 UI 自动化能力"的唯一门面。发送三态接口(`send_*_state`)在它上面。调用方永远只看引擎,不碰适配器。
- **适配器 (Adapter)**:针对特定微信版本(4.1.x)的 UI 自动化实现。引擎之下、截图/OCR/人味模拟之上的模块。
- **三态 (Tri-state)**:发送结果的唯一口径 —— `sent` / `failed` / `uncertain`。`uncertain` 表示按了发送但无法确证结果,调用方**不得**盲目重发。任何上报"成功"的路径必须有三态依据。
- **发错人 (Wrong-target)**:最严重的事故等级。防发错人链条:目标名归一化 → 守卫匹配 → 输入前复核标题 → Enter 前最后一道闸 → 发送后双条件验证 + 绿色气泡复核。

## 监控域

- **监听 (Listener)**:未读监控主循环(声音触发 + 轮询 + 持续监听 burst)。唯一的消息采集入口。
- **红点 (Badge)**:会话列表头像上的角标。两类:**未读**(大红点,可带数字)与**免打扰**(小红点无数字,跳过)。`kind` 只有 `unread / muted / none`。
- **活跃会话 (Active Session)**:持续监听模式下按活跃分(衰减 ×0.85/tick,命中 +boost)选出的当前盯防群。
- **空闲浏览 (Idle Browse)**:无任务时把浏览器置前、看 1-2 个网页模拟人类。默认关,全局快捷键或后台开启。
- **让出 (Yield)**:`Ctrl+Alt+Y` 请求的 30 秒窗口,期间宿主暂停一切抢鼠标动作,把机器还给人。

## 会话/息屏域

- **挂起保活 (Hang-to-console)**:`tscon <sid> /dest:console`,把 RDP 断开后的会话挂回本机控制台,防止锁屏导致合成输入失效。
- **息屏 (Screen Off)**:系统级关闭显示器背光(SC_MONITORPOWER),**后台照常运行**。配套保黑看门狗(被物理输入唤醒则压回)与物理输入吞没(合成输入放行)。
- **保黑 (Keep-black)**:息屏期间周期性重新压黑显示器的看门狗线程。
- **物理输入吞没 (Input swallow)**:息屏期间用低级钩子吞掉**人类**键鼠事件(ESC 除外),带 `LLKHF_INJECTED` 标记的**合成**事件(宿主自己的自动化)不受影响。

## 数据域

- **消息库 (Message Store)**:唯一的收发消息持久层,SQLite(WAL)单库 `data/magpie.db`。收到的消息与发出的消息同表同源。
- **导出 (Export)**:把消息库按会话导出为 Excel 友好 CSV(UTF-8 BOM)到 `data/exports/`。用户查看/修改数据的入口:Web 后台 + 导出 CSV;**不直接编辑 .db**。
- **插件设置 (Plugin Settings)**:`plugins/config/<name>.json`,人手可编辑,保存即热生效。与消息库分离 —— 配置永远是人读的 JSON。

## 插件/生态域

- **插件 (Plugin)**:放 `plugins/` 目录的 Python 包,以 `PLUGIN` dict 自述,约定 `on_<event>` / `action_<name>`。宿主唯一通过 PluginManager 调度。
- ** LeadsLinker**:官方插件套件名(插件 `leads_forwarder` + 浏览器扩展),当前大版本 V3。职能:抖音来客线索 → 微信群 @收单员。
- **浏览器扩展 (Extension)**:`extension/` 目录的 Chrome MV3 扩展,跑在抖音来客页面上,经 OneBot HTTP 与宿主通信。
- **调试通道 (Debug Channel)**:AI 经 OneBot action `debug_*` 提交 DOM 命令 → 扩展轮询执行 → 回传结果的远控链路。仅调试用。
- **受理 (Acceptance)**:线索被 @ 的人回复 `1/0/好的/没空` 等整词后的确认/拒收状态;超时自动转下一人。
