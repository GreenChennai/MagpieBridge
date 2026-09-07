# Human Behavior Recorder

一个把**真人操作**录下来、转成可复用「行为画像」，让 MagpieBridge 的鼠标模拟**基于真实数据**而非猜测的工具。

## 为什么
之前鼠标模拟是靠人估计的（贝塞尔、抖动、停顿都是猜的）。这个工具让**你自己**演示：
1. 移动鼠标去点按钮；
2. 在"类似微信聊天列表"里找目标并点击、滚动。

工具用 Win32 `GetCursorPos` 以 ~80Hz 记录真实轨迹和点击，分析出**真实平均速度 / 峰值速度 / 抖动幅度 / 停顿分布 / 点击驻留**，写成 `human_profile.json`，并生成一段可参考的模拟代码——这样比单纯猜要真实、可靠得多。

## 运行
```bat
python tools\human_behavior_recorder.py
```

### 任务一：按钮点击测试
- 点「开始录制」→ 屏幕出现"目标"按钮（每次随机位置）。
- 你移动鼠标点击它，共 10 次。工具记录每次「移动轨迹 + 点击驻留」。

### 任务二：聊天列表查找测试
- 切换到「聊天列表查找测试」→ 出现一个含随机人员名（张三/李四/陈晓铭/安信德…）的伪列表，顶部提示「请点击：XXX」。
- 你查找（可**鼠标滚轮**滚动）并点击目标，共 3 轮。工具记录「查找耗时 + 滚动 + 点击轨迹」。

## 输出
- `tools/human_profile.json` —— 实测画像：
  ```json
  {
    "move": { "avg_speed_px_s": ..., "peak_speed_px_s": ..., "avg_tremor_px": ...,
              "avg_pause_count": ..., "avg_pause_ms": ... },
    "click": { "avg_dwell_ms": ... }
  }
  ```
- 界面下方生成一段「模拟参考代码」（`human_move_to` 用实测速度/停顿/抖动生成拟人轨迹）。
- 点「统计/生成模拟代码」即可写盘并显示。

## 如何让 MagpieBridge 用上实测画像
把 `human_profile.json` 放到 **exe 同目录**（或 `tools/`）即可，程序启动会自动加载并让 `HumanSimulator` 使用实测的 `avg_speed_px_s` / `avg_tremor_px` / `click_dwell_ms`（缺失则回退 config 默认值）。重新录制后覆盖该文件、重启程序即生效。

## 说明
- 需 Windows（`GetCursorPos`）。
- 录制时请像平时一样自然地移动；数据越自然，模拟越拟人。
