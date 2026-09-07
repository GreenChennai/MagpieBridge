# 同类项目借鉴分析（v0.2.7）

调研结论：所有 hook/注入项目（WeChatFerry、ComWeChatRobot）均已归档；**UI Automation / 非注入是唯一可持续路线**。

## 已 Clone 项目（E:\平日资料\GitHub\）

| 项目 | Stars | 路线 | 结论 |
| --- | --- | --- | --- |
| wxauto | 7.3k ✅活跃 | UI Automation（微信 3.9） | **最相关**，借鉴其流程级健壮性 |
| Wechaty | 23k ✅活跃 | 多协议 SDK | 类型化事件总线设计（我们插件系统已有等价物） |
| FlowBot | 1.4k ✅活跃 | Android 无障碍 | 仓库不存在；Android 方案参考价值低 |

## 关键发现

1. **微信 4.x UIA 树不可用**：实测微信 4.1.13 主窗口不在 UIA 根树（自绘 UI + 控件属性随机化）→ **OCR + 坐标是 4.x 正确路线**，UIA 辅助定位不引入。
2. wxauto 也识别"XX条新消息"按钮（`wxauto.py:169`）——印证本软件监听逻辑。
3. wxauto 依赖 `pyperclip`、`win32gui` 等（未集成，仅参考）。

## 已落地借鉴（v0.2.7）

| # | 借鉴点 | 来源 | 实现 |
| --- | --- | --- | --- |
| 1 | **@ 弹窗确认流程** | wxauto `SendMsg`（输入 @名字 → 检测 `ChatContactMenu` → ENTER） | `adapter.send_at`：输入 `@昵称` → OCR 检测候选面板（`_at_candidate_open`）→ ENTER 确认 → 正文 |
| 2 | **粘贴后输入框内容验证** | wxauto `GetValuePattern().Value` 循环重试 | `adapter._ensure_input_content`：粘贴/输入后截图输入框验证有内容（暗像素 >20），空则重试 3 次 |
| 3 | **微信时间解析** | wxauto `ParseWeChatTime` | 新 `core/wechat_time.py`：`HH:MM`/`昨天 HH:MM`/`星期X HH:MM`/`YYYY年M月D日 HH:MM` → `YYYY-MM-DD HH:MM:SS` |
| 4 | **微信版本检查** | wxauto `_checkversion` | `adapter.check_version()`：ctypes 读 exe FileVersion（version.dll！）+ 前缀匹配配置，不匹配告警 |
| 5 | Enter 键发送 | wxauto `SendKeys('{Enter}')` | `HumanSimulator._send_enter`（VK_RETURN） |

## 附带修复

- `_launch_wechat` 候选路径顺序：**Weixin.exe(4.x) 优先**，原顺序会误启动 3.9 旧版 WeChat.exe
- `_get_file_version` 用 **version.dll**（版本 API 不在 kernel32）

## 明确不引入

- UIA 元素定位（4.x 无树）
- DLL 注入（已归档路线，封号风险）
- 多语言抽象（用户中文环境）
