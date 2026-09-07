# LeadsLinker V3(官方插件套件)

抖音来客(life.douyin.com)投流线索 → 自动转发微信群并 @收单员。

```
抖音来客页面 ──检测线索──→ 浏览器扩展(MV3)
                                │ POST http://127.0.0.1:3000/send_leads (OneBot)
                                ▼
                     鹊桥插件 leads_forwarder ──CSV 台账 + 排班轮转──→ 微信群 @收单员
                                ▲
                     AI 调试通道(debug_* action ↔ 扩展轮询执行)
```

## 组成

| 部分 | 位置 | 版本 |
|---|---|---|
| OneBot 插件 | `plugins/leads_forwarder/` | 3.0.0 |
| 浏览器扩展 | `extension/`(Chrome MV3,加载已解压扩展) | 3.0.0 |

## 插件能力

- **线索转发**:接收 → 在途去重 → 月度 CSV 台账(`~/LeadsLinker/leads/`)→
  时段排班轮转选群选人(工作日/周末、跨午夜、暂停时段)→ 文字/图片卡片发送
  (图片三级渲染回退:系统 Edge headless → playwright → Pillow);
- **受理跟踪**:被 @ 的人回复 `1/0/好的/没空`(整词判定,拒收优先)→
  确认/拒收/超时自动转下一人;阻塞时段暂存队列到点补发;
- **群聊命令**:`/ll 统计|月统计|日统计|查 <关键词>|待受理|转单`;
- **调试通道**:AI 经 `POST :3000/debug_query` 提交 DOM 命令,扩展 1.5s 轮询
  执行并回传结果(仅调试用);
- **群名安全**:匹配直接委托宿主 `magpie.core.target_guard`(禁子串、拒同名
  编号群),不存在两份阈值漂移的副本。

## 扩展能力

四级联系方式提取(手机号/微信标注/独立微信号/二维码)、MutationObserver + 轮询
双保险、自动点开会话、转发队列指数退避(5s→5min 永不放弃)、SW 保活心跳、
"强劲模式"(单标签置前 + 定时刷新)、popup 四页 UI(线索/配置/测试/调试)。

## 配置

- 插件设置:后台「插件 → leads_forwarder → 设置」(发送格式/成员库/时段排班/
  超时/调试开关),或 `POST :8080/api/plugins/leads_forwarder/settings`;
- 目标群/成员:插件设置 `groups`/`members`(与 `~/LeadsLinker/config.json` 兼容);
- 扩展:popup「配置」页填 OneBot 地址(默认 `http://127.0.0.1:3000`)。

## 故障排查(最常见)

| 症状 | 处置 |
|---|---|
| 扩展转发失败 | 鹊桥没启动(探活 `GET :3000/status`);`RETRY_QUEUE` 会自动补发 |
| 线索不检测 | 确认页面是来客"会话"页;popup「调试」页跑选择器自检 |
| 发出但没人收到 | 看鹊桥日志是否有"未能置前"(RDP 断开自愈中),邮件告警会通知 |
