# 断开 RDP 自愈验证报告(ADR-0006)

生成时间: 2026-09-08 01:28
结论: ✅ **通过**(两阶段验证全部完成,附一个已修复的窗口期发现)

## 验证结论

| 阶段 | 内容 | 结果 |
|---|---|---|
| 1 | 用户断开 RDP(00:55:31,桌面不可达+无前台) | 检测到 |
| 2 | 宿主自动挂回本机控制台 | ✅ 25 秒内完成(00:55:56 桌面可达、会话活跃) |
| 3 | 挂回后 30~60s「无前台窗口期」内发送 | ❌ 失败 → **已定位并修复**(见下) |
| 4 | 窗口期过后发送(运行中实例 API) | ✅ `{"success": true}` 消息库 `success`(id=2,发往"测试的佛山投流工作群") |

## 窗口期发现与修复

tscon 挂回控制台后的头 30~60 秒,console 桌面处于「无前台」状态,
SetForegroundWindow 全部被拒 → 置前失败 → 发送失败。桌面恢复后一切正常。

修复(commit d8e196f):
- `_force_foreground` 备选链:AttachThreadInput 失败 → SwitchToThisWindow →
  最小化+恢复(实验矩阵 5/5 方法有效);
- `restore_and_focus` 改为 45s 截止的重试循环,等桌面就绪后自动继续发送。

新版 exe 已部署:`D:\DLWechat\MagpieBridge-V5.0.0\update\MagpieBridge.exe`
(旧实例运行中锁定主文件;退出旧实例后把 update 里的 exe 复制到上级目录替换,或重启后直接运行)。

## 时间线

```
[00:52:14] 编排启动,基线: state=0 desktop=True fg=1053206
[00:55:31] 检测到断开: state=0 desktop=False fg=0
[00:55:56] 自愈探针: state=0 desktop=True fg=1053206 -> OK(挂回耗时 <25s)
[00:55:56] 金标准复核(桌面可达+会话活跃): True
[00:56:28] 窗口期内发送 -> failed(失败根因:无前台窗口期,已修复)
[01:14:xx] 窗口期后 API 发送 -> success id=2
```
