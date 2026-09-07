# LeadsLinker

抖音来客线索检测与微信自动转发系统。

## 项目结构

```
LeadsLinker/
├── extension/              # 浏览器插件 (Chrome Manifest V3)
│   ├── manifest.json
│   ├── background.js       # 后台脚本：接收线索，转发到OneBot
│   ├── content.js          # 内容脚本：检测抖音来客页面线索
│   ├── popup.html          # 弹窗界面
│   ├── popup.js            # 弹窗逻辑
│   └── styles.css          # 样式文件
├── onebot-plugin/          # OneBot 插件 (放入MagpieBridge/Plugins/)
│   └── leads_forwarder/    # 文件夹插件
│       ├── __init__.py     # 插件入口 (PLUGIN 元信息 + 事件/action)
│       └── renderer.py     # 图片渲染子模块 (playwright/Pillow)
└── shared/
```

## 工作流程

```
抖音来客会话页(life.douyin.com/p/liteapp/leads_cs/chat/session)
    │
    ▼
浏览器插件 (content.js 检测线索 + 抓取二维码尽力而为)
    │
    ▼
浏览器插件 (background.js 转发：name/phone/wechat/note/qrcode/avatar)
    │ HTTP POST
    ▼
OneBot 插件 (leads_forwarder)
    │ 1. 保存 Excel/CSV 到本地备份
    │ 2. 按 send_mode 生成消息：
    │    • text  → 完整文字版（含联系方式）
    │    • image → ① @消息 + 主要消息 → ② 图片卡片（含联系方式：手机号/二维码/微信号，更美观）
    │ 3. 调 engine.send_at_message + send_image_bytes
    ▼
MagpieBridge
    │
    ▼
微信群聊 (@指定人 + 卡片详情)
```

> **目标页面**：实际投递线索的页面是会话页 `https://life.douyin.com/p/liteapp/leads_cs/chat/session?groupid=...&life_biz_view_id=22`（不是官网营销首页）。需先在抖音来客手机版/App/已登录网页端登录账号，会话页才有线索数据。

## 安装与配置

### 1. 浏览器插件安装

1. 打开 Chrome 浏览器，访问 `chrome://extensions/`
2. 开启「开发者模式」
3. 点击「加载已解压的扩展程序」
4. 选择 `LeadsLinker/extension` 目录

### 2. OneBot 插件安装

将 `onebot-plugin/leads_forwarder/` 文件夹复制到 MagpieBridge 程序目录的 `Plugins/` 文件夹
（即 `Plugins/leads_forwarder/__init__.py` + `renderer.py`），重启程序或调用
`POST /api/plugins/reload` 热加载。

### 3. MagpieBridge 配置

确保 MagpieBridge 已配置微信监控，且目标群在 `monitor.listen_contacts` 中。

### 4. 插件设置（两种方式任选，后台推荐）

**方式 A：MagpieBridge 控制台（v0.2.8+ 推荐）**

打开 Web 控制台 →「插件管理」→ `leads_forwarder` 行点击「**设置**」，在表单中配置：

- **目标群**: 每行一个群名（多群轮转）
- **成员列表**: 每行一个人名
- **工作日 @顺序 / 周末 @顺序**: 每行一个，按顺序轮流 @
- **回复超时**: 超时未受理自动转发下一人（分钟）
- **发送格式**: `text`=文字版 / `image`=图片版卡片

保存后立即生效（无需重载插件），配置写入 `Plugins/config/leads_forwarder.json`，
并同步到本地 `~/LeadsLinker/config.json`（保持浏览器插件读取一致）。

**方式 B：浏览器插件弹窗**

在插件弹窗的「配置」标签页中：

- **OneBot 插件地址**: 默认 `http://127.0.0.1:3000`
- **微信群配置**: 每行一个群名
- **@人员配置**: 每行一个人名
- **工作日/周末 @顺序**: 按顺序排列

**方式 C：OneBot action（供程序/脚本调用）**

```bash
# 读取配置
curl -X POST http://127.0.0.1:3000/get_config -H "Content-Type: application/json" -d '{"action":"get_config"}'

# 更新配置（patch 局部更新，textarea 换行分隔）
curl -X POST http://127.0.0.1:3000/update_config -H "Content-Type: application/json" \
  -d '{"action":"update_config","params":{"patch":{"groups":"销售一组\n销售二组","members":"张三\n李四","reply_timeout":8}}}'
```

## 调试通道（AI 远程抓取页面 DOM）

> 目的：在生产环境里让 AI/开发者通过本机 HTTP 获取**抖音来客页面的真实 DOM 信息**，
> 用来定位"识别不到账号/线索"的原因并重设计插件选择器。仅绑定本机 127.0.0.1。

### 架构

```
AI ──POST /debug_query──▶ MagpieBridge(:3000, leads_forwarder 插件中继)
                              │  (命令队列)
        ┌──扩展轮询 debug_poll (1.5s)──┘
[浏览器扩展 background] ──▶ 抖音来客 content script (debug-content.js) 执行
                              │
   AI ◀─debug_result─────────┘  (结果在插件内存保留 10 分钟)
```

### 版本要求

- 插件 `leads_forwarder` **v4.0+**（复制新文件到 `Plugins/leads_forwarder/`，`POST /api/plugins/reload` 热加载）
- 浏览器扩展 **v1.1.0+**（重新"加载已解压的扩展程序"）
- 开关：插件设置 `debug_enabled`（默认开）；扩展弹窗「调试」页可关闭

### AI 常用命令（curl 示例）

```bash
# 0. 检查扩展是否在线
curl -s -X POST http://127.0.0.1:3000/debug_status -H "Content-Type: application/json" -d '{"action":"debug_status","params":{}}'

# 1. 完整快照（账号候选 + 标签 + 消息项 + 线索标记 + 页面信息）
curl -s -X POST http://127.0.0.1:3000/debug_query -H "Content-Type: application/json" \
  -d '{"action":"debug_query","params":{"cmd":"snapshot","timeout":10}}'

# 2. 批量测试候选选择器（重设计 content.js 前必用）
curl -s -X POST http://127.0.0.1:3000/debug_query -H "Content-Type: application/json" \
  -d '{"action":"debug_query","params":{"cmd":"test_selectors","params":{"selectors":[".byted-tab-item",".life-tab-active","[class*=chat-item]"]},"timeout":10}}'

# 3. 定位"已留资/广告源/经营源"标记所在的真实元素路径
curl -s -X POST http://127.0.0.1:3000/debug_query -H "Content-Type: application/json" \
  -d '{"action":"debug_query","params":{"cmd":"find_text","params":{"text":"已留资","max":10},"timeout":10}}'

# 4. 抓取指定容器的 outerHTML（分析真实 class 结构）
curl -s -X POST http://127.0.0.1:3000/debug_query -H "Content-Type: application/json" \
  -d '{"action":"debug_query","params":{"cmd":"html","params":{"selector":"[class*=chat-item]","limit":3,"maxLen":4000},"timeout":10}}'

# 5. 执行任意 JS（isolated world；返回 JSON 可序列化结果）
curl -s -X POST http://127.0.0.1:3000/debug_query -H "Content-Type: application/json" \
  -d '{"action":"debug_query","params":{"cmd":"eval_code","params":{"code":"document.querySelectorAll(\"[class*=item]\").length"},"timeout":10}}'

# 6. 整页 HTML / DOM 结构概览 / 网络资源 / 动态加载观察
#    cmd 支持: capture / walk / network / mutation_test / page_info / account_candidates / current_account_guess / tabs / message_items / markers
```

全部命令列表与参数见 `extension/debug-content.js` 顶部注释。

### 异步工作流（可选）

```bash
# 提交后立即返回 cmd_id
curl -s -X POST http://127.0.0.1:3000/debug_submit -H "Content-Type: application/json" \
  -d '{"action":"debug_submit","params":{"cmd":"snapshot"}}'
# 稍后拉取
curl -s -X POST http://127.0.0.1:3000/debug_fetch -H "Content-Type: application/json" \
  -d '{"action":"debug_fetch","params":{"cmd_id":"dbg_..."}}'
```

> 注意：`debug_query` 内部会等待结果（默认 8s，最大 60s）。结果在内存保留 10 分钟，`debug_fetch` 取走后即删除。
> 扩展轮询间隔 1.5s；OneBot 未启动时自动退避到 5s。

## 故障排查

### 转发没反应 / 测试发送失败

**最常见原因：MagpieBridge 程序未启动。** 浏览器插件把线索 POST 到 `http://127.0.0.1:3000`（OneBot HTTP），
程序未运行时连接会被拒绝，转发队列静默重试。

排查步骤：

1. **确认程序在运行**：任务管理器有 `MagpieBridge.exe`，或命令行 `netstat -ano | findstr :3000` 有 LISTENING
2. **插件弹窗检测**：打开浏览器插件 →「配置」标签页 → 点击「**检测连接**」：
   - 显示「OneBot 已连接」= 正常
   - 显示「OneBot 未连接」= 程序未启动或端口不对（在输入框改成实际端口后重新检测）
3. **查看转发错误**：「线索列表」页顶部若出现红色「⚠ 转发到 OneBot 失败」条 = 转发失败的具体原因与时间

### 其他

- 收到线索但没发到微信群：插件「设置」→ 确认已配置目标群（群名须与微信中完全一致）
- 提示 Unknown action：`Plugins/` 目录缺 `leads_forwarder.py`，或插件未加载（Web 控制台「插件管理」查看）
- **检测不到任何线索**（插件运行但「线索列表」一直为空）：切换到插件弹窗「**诊断**」标签页 → 点击「**立即诊断**」。诊断会列出当前页面所有候选选择器的命中数：命中 ≥1 表示选择器生效，全 0 则需要按真实 class 调整。点「**复制诊断 JSON**」把 prefixTop / candidateHits / 留资样本 HTML 发回维护者用于重写 `content.js` 的 `SELECTORS` 表。
- 验证扩展是否真正加载到当前页面：在页面 DevTools Console 执行 `Array.from(document.styleSheets).find(s => { try { return Array.from(s.cssRules||[]).some(r => r.cssText.includes('slideIn')) } catch(e){return false} })`。返回非 undefined = `content.js` 已成功注入。

---

## 插件功能

### 浏览器插件

- 实时检测 `https://life.douyin.com/p/liteapp/leads_cs/chat/session?...` 会话页的投流线索
- 自动识别手机号、微信号、姓名、备注等信息
- 页面变化自动检测（MutationObserver + fetch/XHR 监听）
- 支持多账号切换
- 支持系统通知、保持活跃、自动刷新
- **页面诊断**（v1.0+）：检测不到线索时自动 / 手动构建诊断，输出 class 前缀分布与候选选择器命中数，便于按真实 DOM 微调选择器

### OneBot 插件

- 接收浏览器插件转发的线索数据
- 保存 Excel 到本地 `~/LeadsLinker/leads/` 目录（按日期命名）
- 生成图片/文本消息
- 通过 `engine.send_at_message` 发送到微信群并 @指定人
- 支持「线索统计」命令查询今日线索数
- **图片版（v3.4.1+）**：发送结构为「① @消息+主要消息 → ② 图片卡片」
  - 图片卡片含联系方式区块：📞 手机号（蓝色大号等宽突出）、📱 微信二维码（白底圆角 + 提示文案）、💬 微信号（绿色），加底部回复指引 footer（脉冲点 + "请在 X 分钟内回复"）
  - 实时截图示例见 `debug_shots/lead_card_qr.png` / `lead_card.png`

## 依赖

### 浏览器插件

无额外依赖，Chrome Manifest V3。

### OneBot 插件

- openpyxl (可选，用于保存 Excel；未安装时自动降级为 CSV)
- playwright (可选，用于渲染图片；未安装时只发文本)

## 更新日志

### v3.4.1（2026-08-31）— 图片版发送结构升级 + 联系方式卡片 + 二维码支持

> **2026-08-31 续**：图片卡片字体/icon/层级 三大调整（见下方"v3.4.1 续"章节）

**OneBot 插件**：

- 发送结构重构（image 模式）：① `@消息 + 主要消息`（`📢 新线索提醒（N条），请及时受理！`）→ ② 图片卡片（承载详情 + 联系方式）。原仅发图片（无 @ 提醒）已修正。
- 统一发送函数 `_send_lead_batch`，4 处调用点（`_forward_leads` / `_check_timeouts` 转下一人 / `_handle_reply` 跳过转发 / `_handle_transfer` 转单）全部走同一条路径，行为一致且图片渲染失败自动降级文字版。
- 图片卡片升级：
  - **联系方式区块**（重点突出）：浅紫渐变底 + 圆角 + 虚线分隔
    - 📞 手机号：蓝色大号等宽字体 + 字间距
    - 📱 二维码（如有）：白底圆角图 + 「微信扫一扫」提示文案 + 兜底占位
    - 💬 微信号：绿色 + 微信图标
  - 底部回复指引 footer：深色渐变条 + 脉冲点 + 「请在 X 分钟内回复：1/好的 = 受理，0/没空 = 跳过」
  - header 装饰圆 / 头像首字占位 / 序号徽章 / 类型徽章 / 备注行 等细节微调

**浏览器扩展**：

- `content.js` 新增 `extractQrcode(element)`：扫描消息元素 + 2 层父链中 `img`，优先抓 `data:image/` 内嵌图（最可能二维码且不受 CORS 影响），其次 URL 含 `qr/qrcode/scan` 特征的外链图。尽力而为，会话页未验证时可能抓不到（卡片降级为微信号大号展示）。
- `background.js` 转发 payload 与测试线索增加 `qrcode` 字段透传。

**实测**：HTML 卡片在 Edge 渲染验证（截图见 `debug_shots/`）—— 无二维码版 / 带二维码版均布局完整、配色一致、联系方式一目了然。

### v3.4.1 续（2026-08-31）— 卡片字体/icon/层级 三大调整 + 关键 bug fix

**OneBot 插件图片卡片**：

1. **字体统一为系统「微软雅黑」**
   - 去掉 `@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC...')`（网络加载在微信/无网环境会失败或卡顿）
   - `body font-family: 'Microsoft YaHei', '微软雅黑', sans-serif` 全局继承
   - 数字/英文也走微软雅黑（删除 `.lead-serial` 与 `.contact-value.phone` 的 `font-family: monospace`）
   - 优势：渲染更快、零网络依赖、跨设备一致

2. **emoji 全部改为内联 SVG icon**（feather 风格、MIT 许可、stroke=currentColor）
   - header 铃铛（白） / 二维码四宫格（紫） / 电话听筒（紫） / 微信气泡（绿） / info 圆圈（灰）
   - 颜色通过 CSS class 区分：`.ico-qr/#7c3aed` `.ico-phone/#4f46e5` `.ico-wechat/#07c160` `.ico-info/#9ca3af`
   - 容器 `.contact-ico` 改 flex 居中 22×22px，`.contact-ico svg` 17×17
   - Pillow 降级路径不依赖 emoji（纯正则匹配 class 名），HTML 里 SVG 标签当作未知元素跳过不影响降级

3. **排版层级结构强化**
   - 联系方式区块顶部加 `.block-title`（12px + 字间距 1px + 紫色渐变竖条），文字「联系方式」
   - 完整层级：`card-header` → `lead-card`（`lead-top` 身份层 → `contact-block` 联系方式层 + `block-title` 标题 → `note-row` 备注层）→ `card-footer` 回复指引

**实测**：`debug_shots/lead_card_v2.png`（无二维码）与 `lead_card_v2_qr.png`（带二维码）—— 微软雅黑渲染一致、SVG icon 彩色 stroke、层级清晰。构建目录 V0.4.0 已同步。

### v3.4.0（2026-08-30）

- 图片生成新增 playwright（Chromium）渲染，效果提升；playwright 不可用时降级 Pillow
- `wait_until="networkidle"` 改为 `domcontentloaded` + 8s 超时，避免外部字体 CDN 拉不到卡死

**v3.4.2 关键 bug fix（生产环境未装 playwright 走 Pillow 降级）**：
- 修正 `_render_with_pillow` 正则匹配 class 名：v3.4.0 重构 contact-block 时把 `info-value` 改为 `contact-value`，但 renderer.py 未同步，导致 Pillow 降级渲染的图只有姓名、缺少电话/微信/备注
- 同步：phone → `class="contact-value phone"`, wechat → `class="contact-value wechat"`, note → 在 `.note-row` 内的 `<span>`（前一个 span 是"备注"字样）
- **影响范围**：v0.3.0 起定论"playwright 不内置（体积大）"→ 用户生产环境实际走 Pillow 降级 → 该 bug 直接影响所有通过图片版发送的真实微信图片（电话/微信/备注字段全部丢失）
- 验证：单测 3 种场景（phone+wechat+note 全有、仅 phone+note、仅 note）正则全部 100% 命中

### v3.3.x（v0.2.8 起）

- 文件夹插件 + 后台设置 UI（PLUGIN["settings"] schema）
- 依赖内置（openpyxl/xlsxwriter/requests；playwright 不内置）
- 浏览器扩展诊断机制（class 前缀分布 + 候选选择器命中数 + popup「诊断」Tab + 复制 JSON）

### v3.0 之前

- 文字版/图片版基础发送、Excel/CSV 备份、@ 顺序轮转、超时自动转下一人、受理回复 1/0 跟踪等核心功能
