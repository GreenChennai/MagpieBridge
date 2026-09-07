# LeadsLinker 生产环境部署与验证 Checklist

> 适用于首次部署 / 升级扩展后回归 / 新设备迁移。

## TL;DR

```
抖音来客会话页  ─→  LeadsLinker 浏览器扩展  ─→  MagpieBridge  ─→  微信群
                         │                            │
                  content.js 检测                  OneBot 插件
                  background.js 转发           leads_forwarder.py
```

任何一环就绪失败，先按本清单逐环验证。

---

## 一、后端 MagpieBridge 就绪

### 1.1 启动前确认
- [ ] 微信 4.x 已登录
- [ ] `E:\平日资料\构建\MagpieBridge-V0.4.0\MagpieBridge.exe` 存在
- [ ] `Plugins/leads_forwarder/__init__.py` 与 `renderer.py` 已在 V0.4.0 程序目录的 `Plugins/leads_forwarder/` 下
- [ ] `config.json` 中 `monitor.enabled=true`，`monitor.listen_contacts` 含目标群（如「测试捏」）
- [ ] `config.json` 中 `web.port=8080`、`onebot.http_port=3000`、`onebot.ws_port=3001`（默认即可）

### 1.2 启动
```powershell
# PowerShell
& "E:\平日资料\构建\MagpieBridge-V0.4.0\MagpieBridge.exe"
```

### 1.3 健康检查
```powershell
# OneBot 在线？
curl http://127.0.0.1:3000/status

# 插件已加载？
curl http://127.0.0.1:3000/api/plugins

# Web 控制台可达？
curl http://127.0.0.1:8080/
```

期望响应：`{"online":true,"good":true,...}` / 插件列表含 `leads_forwarder` / 控制台返回 HTML。

---

## 二、浏览器扩展就绪

### 2.1 加载
1. Edge/Chrome → `edge://extensions`（Chrome 为 `chrome://extensions`）
2. 打开「开发人员模式」
3. 「加载解压缩的扩展」→ 选 `E:\平日资料\GitHub\LeadsLinker\extension`

### 2.2 验证扩展已加载
- 扩展列表出现「**LeadsLinker - 线索转发**」且开关开启
- 浏览器工具栏出现扩展图标

### 2.3 OneBot 连接配置
1. 扩展图标 → 弹窗 → 「**配置**」Tab
2. 「OneBot 插件地址」填 `http://127.0.0.1:3000`（默认即此）
3. 点「**检测连接**」→ 显示「OneBot 已连接」= OK

---

## 三、会话页检测

### 3.1 打开目标页面
```
https://life.douyin.com/p/liteapp/leads_cs/chat/session?groupid=1860525114613929&life_biz_view_id=22&life_account_biz_ids=
```

未登录会自动重定向到 `https://life.douyin.com/p/login`。需先在另一标签页登录抖音来客（或手机端 App 扫码）。

### 3.2 验证 content.js 已注入
页面 DevTools Console（F12）执行：
```js
Array.from(document.styleSheets).find(s => {
  try { return Array.from(s.cssRules||[]).some(r => r.cssText.includes('slideIn')) }
  catch(e) { return false }
})
```
返回非 `undefined` = content.js 成功注入。

### 3.3 检测是否抓到线索
- 扩展图标 → 「线索列表」Tab：随时间累积新的线索卡片
- 若长时间无新线索：转「**诊断**」Tab →「**立即诊断**」

---

## 四、诊断 → 反馈 → 微调循环

### 4.1 触发诊断
三条路径任选：
1. **自动**：扩展检测连续 3 次未命中 → 自动构建诊断
2. **启动时**：每次会话页加载后 1.5s 自动构建首次诊断
3. **手动**：扩展弹窗 →「诊断」Tab →「立即诊断」按钮

### 4.2 看诊断结果
诊断卡片会列出：
- **Class 前缀 Top15**：如 `life-:264|src-:45|...`（抖音来客业务前缀是 `life-`）
- **候选选择器命中**：每行 `[命中数] 选择器`。命中 ≥1 = 选中；全 0 = 该选择器失配
- **DOM stats**：总元素 / 带 class 元素数
- **留资样本 HTML**：包含「已留资」「广告源」「经营源」文字的元素父链（≤1500 字符）

### 4.3 命中模式判断

| 模式 | 含义 | 下一步 |
|---|---|---|
| activeTab / messageItem 都 ≥1 | 选择器命中 | 等待线索自动检测，无需调整 |
| activeTab = 0 | 没找到激活的 Tab，可能是当前未在「会话」Tab | 切到会话 Tab 重试诊断 |
| messageItem = 0 | 找到 Tab 但没找到消息项 | 「复制诊断 JSON」发回维护者 |
| 所有 group 全 0 | 业务前缀假设错误（如 liteapp 用 `cs-` 而非 `life-`） | 「复制诊断 JSON」发回维护者 |

### 4.4 反馈
- 点「**复制诊断 JSON**」按钮 → 剪贴板有精简版 JSON
- 粘贴给维护者（AI / 自己）
- 维护者修改 `extension/content.js` 的 `SELECTORS` 表，保存文件后扩展 popup「**线索列表**」Tab → 看变化或重新「**立即诊断**」

---

## 五、端到端测试（验证全链路）

### 5.1 用「发送测试线索」按钮
1. 弹窗 → 「**测试**」Tab
2. 姓名/电话/微信/备注/类型填好
3. 点「**发送测试线索**」

期望响应：
- `success: true` + `sent: N`（一条线索送达）
- 目标群（如「测试捏」）收到 @某人的测试消息
- 弹窗显示「成功发送 N 条测试线索」

### 5.2 真实线索自动化
- 保持会话页打开 + 后端运行 + 微信已登录
- 抖音来客有新线索进入 → 扩展自动检测 → 转发 → 微信群 @收单员

---

## 已知风险与对策

| 风险 | 触发条件 | 对策 |
|---|---|---|
| content.js 选择器失配 | 抖音来客 liteapp 重构 DOM | 走第四节诊断反馈循环 |
| 微信 OCR 选错群 | 微信界面变更 / 多窗口重叠 | MagpieBridge TUI 有手动接管入口 |
| 后端崩溃 / 微信掉线 | 长时间运行 | 后端日志 `logs/msgpushwechat.log`；扩展「转发错误」条会显示 |
| 抖音来客重定向到登录页 | Cookie 过期 | 在原浏览器重登，会话页自动恢复 |

---

## 升级扩展

扩展目录是源码（未打包成 .crx）。修改 `extension/*.js` / `popup.html` 后：

1. `edge://extensions` 找到 LeadsLinker → 点「**重新加载**」按钮（🔄 图标）
2. 刷新会话页 → content.js 重注入，新版本生效

无需重启 MagpieBridge（除非改了插件 Python 端）。