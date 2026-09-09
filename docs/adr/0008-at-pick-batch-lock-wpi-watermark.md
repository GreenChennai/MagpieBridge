# ADR-0008: @ 选择三态策略、批次锁、WPI 渲染与卡片水印

日期:2026-09-09
状态:已接受(批次锁/WPI/水印已现场验证;@ 唯一候选验证与折叠置顶场景见"已知边界")

## 背景(五项现场问题)

1. @"Green_Chennai" 有唯一候选但软件判定无候选,退化为粘贴直发;
2. 长名成员("安信德&创客龙 张大山/李钊是")打前缀时候选全部截断为
   "安信德&创客龙...", 无法唯一认定;
3. 多线索并发到达时, 宿主 op_queue 只保证单步串行 → 出现
   图1/图2/联1/联2/@1/@2 交错刷屏;
4. 卡片渲染质量/后端希望整合用户自己的 WPI-noGUI-cli(网页→PNG 导出器);
5. 卡片需要"抖音来客"大字低调水印。

## 决策

### @ 选择(wechat_adapter)

- **归一化**:`_norm_id()` 抹掉空格/下划线/@/省略号/大小写——微信面板插入
  的是成员显示名("Green Chennai"), 配置是群名片("Green_Chennai"),
  旧比较永远失败;
- **三态**:`_try_pick_at_member` 返回 (selected, rows, sim, **ambiguous**);
  `_try_search_and_pick`/`_type_at_and_pick` 透传 "picked"/"ambiguous"/"none";
- **send_at 策略**:picked → Enter chip + 补正文;ambiguous → 清空面板输入,
  粘贴"@全称 正文"直发(消息内容明确记录了 @ 谁;认错人=配置的成员名
  不精确,责任口径清晰);none → 绿色气泡复核后同样粘贴直发,绝不丢提醒。

### 批次锁(plugins/leads_forwarder v3.0.2)

`_forward_task` 持模块级 `_batch_lock`:多线索同时到达时按 create_task 顺序
**线索级串行**——每条线索的"图片→联系方式→@销售"完整执行完才开始下一条。

### 渲染(renderer v3.0.2)

四级回退:**WPI-noGUI-cli(首选)→ Edge headless → playwright → Pillow**。
WPI(GreenChennai/WPI)是用户自己的网页→PNG 导出器;exe 放 `tools/WPI/`
自动启用,`MAGPIE_WPI` 环境变量可覆盖。现场日志多次"WPI 渲染成功"。

### 卡片水印

`.dy-watermark`:96px/900 字重、"抖音来客"四字、rotate(-9°)、
`rgba(91,95,232,0.055)`——大、但正常看忽略仔细看可见。修复了
`.card > div` 特异性(0,2,1)覆盖 `.dy-watermark`(0,1,0)定位的问题
(用 `:not(.dy-watermark)` + 提升特异性双保险)。

## 热修架构教训(plugins/hotfix_foreground v1.7.5)

- **wrapper 闭包绑定旧模块 globals** → reload 后逻辑不更新。修复:wrapper
  内经 `sys.modules[__name__]` 动态取最新 impl;重装无条件(marker 只防
  orig 双重捕获);
- **from-import 绑定分散**(target_guard/adapter/ocr 各有一份 name_matches
  引用):patch 必须逐一覆盖,或改函数本体;
- **大小写拼写**(Wechat4xAdapter vs WeChat4xAdapter)在 try/except warning
  里被吞,曾让三个补丁静默失效——补丁必须自证安装(打安装日志并核对)。

## 已知边界

- **折叠置顶场景**:目标群被置顶且微信折叠置顶区时,主列表不可见。已实现
  两层对策(OCR 定位"折叠置顶聊天"按钮点击展开;搜索框确定性定位),但
  微信重登后的折叠状态下,自动展开点击尚未完全可靠(按钮已可定位点击,
  展开后重扫仍偶发找不到)。**人工一步**:在微信里展开一次折叠置顶,或
  取消目标群置顶,即恢复全部自动化。
- 微信 @ 候选面板在无人值守/息屏组合下的弹出可靠性依赖窗口激活状态;
  粘贴全称降级(策略 2)是该场景的兜底,消息仍会发出且内容明确。
