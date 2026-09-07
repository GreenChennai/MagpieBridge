# LeadsLinker 浏览器插件生产环境改造（2026-08-31）

## TL;DR

把原硬编码 `byted-/csUI-MessageNickname` 等猜测选择器全部替换为抖音来客真实业务前缀 `life-` + 多级降级；新增「页面诊断」机制（候选选择器命中数 + class 前缀分布 + 留资样本 HTML）一键发回，便于根据真实 DOM 微调。

## 背景

抖音来客会话页 `https://life.douyin.com/p/liteapp/leads_cs/chat/session?...` 需登录才能访问。用户在他处浏览器已登录，不愿重复扫码。agent-browser 验证栈（系统 Edge + `--extension`）就绪但因未登录只能看到营销页/登录页，无法进会话页。

agent-browser eval 扒取抖音来客官网 559 个 class 元素：业务前缀确认为 **`life-`**（264 个），`src-`（474）是 CSS Modules 哈希每次构建变。原选择器全部失配。

## 交付物

| 文件 | 改动 |
|---|---|
| `extension/content.js` | 选择器表全改为 `life-*` + 多级降级；新增 `trySelectors` 统一入口；新增 `buildDiagnostic` / `triggerDiagnostic`；新增 `missStreak` 计数；`init()` 启动后 1.5s 自动建一次诊断 |
| `extension/background.js` | 新增 `PAGE_DIAGNOSTIC` / `GET_DIAGNOSTIC` / `CLEAR_DIAGNOSTIC` / `REQUEST_DIAGNOSTIC` 四种消息处理 |
| `extension/manifest.json` | 加 `tabs` 权限（REQUEST_DIAGNOSTIC 需要 chrome.tabs.sendMessage） |
| `extension/popup.html` | 加「诊断」Tab 与「立即诊断 / 复制 JSON / 清空」按钮 + 渲染区 |
| `extension/popup.js` | 诊断模块：触发 / 渲染（prefixTop + candidateHits 命中绿/未命中红 + DOM stats + 留资样本 HTML 截断 1500 字符）/ 复制 / 清空 |

## 关键决策

1. **`life-` 为业务前缀**：基于抖音来客 559 个 class 元素统计的最大可信前缀
2. **CSS Modules（`src-`）不写死**：每次构建哈希变（`--212e2`）；改用结构匹配兜底
3. **多级降级而非单一精确选择器**：业务前缀 → 通用语义属性（`role=tab aria-selected=true`）→ 通用 class 模糊匹配（`*-active`）→ 字节 csUI 兜底
4. **诊断主动收集**：用户会话页打开时 `init()` 后 1.5s 立即构建诊断 + 连续 3 次未命中自动构建 + popup「立即诊断」手动构建。三条触发路径覆盖首启/失配/调试。
5. **复制精简 JSON**（不含超长 sampleOuterHTML 完整版）：用户一键粘贴给 AI，AI 据此调选择器。

## 验证

| 检查 | 结果 |
|---|---|
| `node --check` content.js / background.js / popup.js 语法 | ✅ 三文件均 OK |
| Edge edge://extensions 显示「LeadsLinker - 线索转发 已开启」 | ✅ |
| `slideIn keyframe` 存在（content.js addStyles 成功） | ✅ |
| init() 1.5s 后 buildDiagnostic 自动触发 | ✅（`<html data-leads-linker-diag-time>` 写入） |
| 诊断收集 prefixTop 与人工统计一致 | ✅（`src-:474\|life-:264\|(none):6\|has-:6\|i-:2` 完全一致） |
| 失配自动诊断（reason: no_active_tab）触发 | ✅（营销页 activeTab 命中 0 → 自动触发） |
| `<html>` dataset 让 main world 能读诊断摘要 | ✅ |
| `window.__LeadsLinker_buildDiagnostic` 在 eval 上下文为 undefined | ⚠️ 误判（isolated world vs main world），**不是 bug** |

## 待用户验证

1. 在已登录抖音来客的 Edge/Chrome 加载扩展：
   `edge://extensions` → 开发者模式 → 加载解压缩的扩展 → 选 `E:\平日资料\GitHub\LeadsLinker\extension`
2. 打开会话页 `https://life.douyin.com/p/liteapp/leads_cs/chat/session?groupid=1860525114613929&life_biz_view_id=22&life_account_biz_ids=`
3. 点扩展图标 → 「诊断」Tab → 「立即诊断」
4. 看候选选择器命中数：若 activeTab/messageItem 都 ≥1 → 选择器命中；若有 =0 → 把「复制诊断 JSON」结果发回，按真实 class 调整
5. 若「测试 → 发送测试线索」打通到「测试捏」群 → 整个链路（抖音页面 → content.js → background.js → MsgPushWechat → OneBot → 微信）通了

## 已修复的关键坑（写入 2026-08-31 记忆）

- content script 在 isolated world，agent-browser eval 在 main world，**不能通过 `typeof window.xxx` 判断扩展是否加载**。必须用 DOM 副作用（如查 `@keyframes slideIn`）。
- `agent-browser open` 等 SPA 页面会卡死，**用 `nohup ... &` 后台启动 + sleep + 单独 eval** 绕过。
- `JSON.stringify(obj, arr)` 第二参数若不是过滤 key 列表会返回 `{}`（replacer 数组陷阱）。