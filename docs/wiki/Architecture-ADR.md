# 架构与决策记录(ADR)

代码库语言规范见仓库根 `CONTEXT.md`(术语表)。本页收录影响后续演进的决策。

## 模块地图

```
main.py                     启动编排:提权/单实例/DPI/控制台固定/快捷键/生命周期
magpie/
├── core/                   深模块层(对外接口小,实现厚)
│   ├── ui_engine.py        引擎门面:发送三态接口、操作队列、人味模拟组合
│   ├── wechat_adapter_4x   微信 4.1.13 UI 自动化实现(OCR+坐标)
│   ├── screen_off.py       系统级息屏:保黑看门狗 + 物理输入吞没 (ADR-0003)
│   ├── session_control.py  RDP 挂回控制台(tscon)+ 防锁屏
│   ├── store.py            统一消息库:SQLite WAL 收发同表 (ADR-0002)
│   ├── capture.py          唯一的 GDI 截图实现(BitBlt/PrintWindow)
│   ├── target_guard.py     防发错人守卫(归一化/匹配/相似度)
│   ├── op_queue.py         全局 UI 互斥 + 串行队列(双超时)
│   ├── human_simulator.py  贝塞尔/抖动/停顿/打字节奏
│   └── ...
├── monitor/                listener(未读监控主循环)/sound_monitor(提示音)/status
├── onebot/                 dispatcher(HTTP/WS 共用语义)+ v11_http + v11_ws
├── plugin/manager.py       插件加载/事件/action/设置持久化
├── admin/command_handler.py 管理指令解析与执行
├── tui/                    Textual 终端界面 + bus(状态总线)
└── web_server.py           FastAPI 管理后台(仅限本机回环)
plugins/                    运行时插件(leads_forwarder = LeadsLinker V3)
extension/                  浏览器扩展(MV3)
```

## ADR 索引(仓库 `docs/adr/`)

- **ADR-0001 更名「鹊桥 MagpieBridge」,版本跳 5.0.0** — 产品是"宿主+插件生态"
  而不只是"推送工具";与 LeadsLinker 家族化命名。
- **ADR-0002 数据层 = SQLite(WAL) 单库 + 人读 JSON 配置 + CSV 导出** —
  按"谁写谁读"分存储:流水给机器(WAL 读写互不阻塞),配置给人(Notepad 可改
  热生效),留档给人(一键 CSV)。用户不直接编辑 .db。
- **ADR-0003 息屏 = 系统级 + 保黑 + 输入吞没** — 否决黑遮罩窗口(z-order
  竞争/不省电/不防误碰);SC_MONITORPOWER 不改显示拓扑,微信不锁。
- **ADR-0004 LeadsLinker V3 并入主仓** — 单一事实源(消灭"源目录→部署目录"
  手工拷贝与 dist 落后);插件群名比对直接 import 宿主 target_guard。
- **ADR-0005 重构策略 = 保留核心 + 修 bug + 去重 + 清死代码 + 补测试** —
  防发错人链路与微信像素坐标强耦合,重写 = 重来一遍所有踩坑。

## 关键设计不变量

1. **发错人 = 最高事故等级**:任何路径拿不准就拒发(`failed` 优于 `uncertain`,
   `uncertain` 优于误发);
2. **失败绝不谎报成功**:OneBot 响应与消息库状态如实映射;
3. **一个会话同一时刻只有一个 UI 动作**:全局 `HOLD_UI_LOCK` + 操作队列;
4. **活跃 RDP 会话绝不被挂起**:tscon 只在断开/锁屏时触发;
5. **插件异常隔离**:单个插件崩溃不影响宿主与其他插件。
