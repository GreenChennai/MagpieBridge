# ADR-0005: 重构策略 = 保留核心,修 bug + 去重 + 清死代码 + 补测试

日期:2026-09-07
状态:已接受

## 背景

V0.4.x 的核心链路(防发错人守卫链、操作队列双超时、发送三态、
人味模拟的贝塞尔/抖动/打字节奏)是按 2026-09-02 审查报告逐项修出来的,
与真实微信 4.1.13 的像素坐标强耦合,校准成本极高,重写=重来一遍所有踩坑。

## 决策

- **保留**:adapter_4x、listener 主流程、target_guard、op_queue、
  human_simulator、red_dot、ocr 的算法主体;
- **修**:息屏 global 丢失、监听去重键不一致、指纹淘汰非 FIFO、
  OneBot HTTP stop() 必炸、_scan_all 变量作用域、websockets 兼容、
  CORS 全开、PatternMatcher 子串串群风险;
- **去重**:三份 GDI 截图 → `core/capture.py`;OneBot HTTP/WS 重复的
  action 分发 → `onebot/dispatcher.py`;web_server 薄化;
- **清死代码**:message_monitor.py、wechat_time.py 整模块及各处无调用函数;
- **补**:`tests/` 单元测试(纯逻辑层:守卫/配置/存储/插件系统/消息提取),
  CI 可跑;真机测试只测发送与息屏两条主链路。

## 否决

- 全量重写(风险与收益不成比例);
- 换 UI 自动化技术路线(hook/注入路线已调研否决,见旧 BENCHMARK_ANALYSIS)。
