# 断开 RDP 自愈验证报告(ADR-0006)

生成时间: 2026-09-08 00:56:31

## 结论

❌ 未完全通过(详见时间线)

## 发送验证

`{"success": false, "message_id": 1} | 消息库最新状态: failed (id=1)`

## 时间线

```
[00:52:14] === 编排启动:等待断开事件 ===
[00:52:14] 探针基线: {"session_state": 0, "input_desktop": true, "foreground": 1053206}
[00:55:31] 检测到断开态: {"session_state": 0, "input_desktop": false, "foreground": 0}
[00:55:31] 断开确认,等待 25s 让宿主自动挂回控制台……
[00:55:56] 自愈探针 #1: {"session_state": 0, "input_desktop": true, "foreground": 1053206} -> OK
[00:55:56] 自愈结论: 成功
[00:55:56] 金标准复核(桌面可达+会话活跃): {"session_state": 0, "input_desktop": true, "foreground": 1053206} -> True
[00:55:56] 触发真实发送 -> 测试的佛山投流工作群
[00:56:28] 发送 API: {"success": false, "message_id": 1}
[00:56:31] {"success": false, "message_id": 1} | 消息库最新状态: failed (id=1)
```
