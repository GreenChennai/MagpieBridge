// LeadsLinker 后台脚本 - 接收线索并转发到OneBot插件

let leads = [];
let forwardingQueue = [];
let isForwarding = false;
let currentAccount = null;

const ONEBOT_URL = 'http://127.0.0.1:3000';

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.type === 'NEW_LEAD') {
    const lead = {
      id: Date.now(),
      timestamp: new Date().toISOString(),
      account: currentAccount,
      forwardState: 'pending',
      forwardMessage: '',
      ...request.data
    };

    // 第二道去重: 同手机号/微信号今天已记录过 → 丢弃(不重复入列表/队列)
    const today = new Date().toISOString().slice(0, 10);
    const isDuplicate = leads.some(l => {
      if (lead.phone && l.phone === lead.phone) {
        const lt = (l.timestamp || '').slice(0, 10);
        if (lt === today) return true;
      }
      if (lead.wechat && l.wechat === lead.wechat) {
        const lt = (l.timestamp || '').slice(0, 10);
        if (lt === today) return true;
      }
      return false;
    });

    if (isDuplicate) {
      console.log('[LeadsLinker] 忽略重复线索(今天已记录):', lead.name || lead.phone);
      sendResponse({ success: true, duplicated: true, leadId: lead.id });
      return;
    }

    leads.push(lead);
    saveLeads();

    forwardingQueue.push(lead);
    saveQueue();
    processForwardingQueue();

    sendResponse({ success: true, leadId: lead.id });
  }

  if (request.type === 'GET_LEADS') {
    const accountLeads = currentAccount ?
      leads.filter(l => l.account === currentAccount) : leads;
    sendResponse({ leads: accountLeads });
  }

  if (request.type === 'CLEAR_LEADS') {
    if (currentAccount) {
      leads = leads.filter(l => l.account !== currentAccount);
    } else {
      leads = [];
    }
    saveLeads();
    sendResponse({ success: true });
  }

  if (request.type === 'CLEAR_QUEUE') {
    // 清空待转发队列: 丢弃排队中/重试中的线索, 重置转发状态
    const cleared = forwardingQueue.length;
    forwardingQueue = [];
    isForwarding = false;
    saveQueue();
    if (currentAccount) {
      leads = leads.filter(l => l.account !== currentAccount);
    } else {
      leads = [];
    }
    saveLeads();
    console.log('[LeadsLinker] 待转发队列已清空:', cleared, '条');
    sendResponse({ success: true, cleared });
  }

  if (request.type === 'RETRY_QUEUE') {
    // 弹窗「立即重试」: ① 驱动本地待转发队列 ② 通知 OneBot 立即冲刷服务端阻塞队列
    // (运营改完排班/放开时段后, 一键把积压发出去, 不必等最多30s的定时循环)
    (async () => {
      processForwardingQueue();
      let server = null;
      try {
        const config = await getConfig();
        const url = (config && config.onebotUrl) || ONEBOT_URL;
        const res = await fetch(`${url}/flush_blocked`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'flush_blocked', params: {} }),
          signal: AbortSignal.timeout(15000)
        });
        const json = await res.json();
        server = (json && json.data) || json;
      } catch (e) {
        server = { ok: false, error: e.message };
      }
      sendResponse({ success: true, pending: forwardingQueue.length, server });
    })();
    return true;
  }

  if (request.type === 'GET_QUEUE_STATE') {
    (async () => {
      let server = null;
      try {
        const config = await getConfig();
        const url = (config && config.onebotUrl) || ONEBOT_URL;
        const res = await fetch(`${url}/blocked_status`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'blocked_status', params: {} }),
          signal: AbortSignal.timeout(4000)
        });
        const json = await res.json();
        server = (json && json.data) || json;
      } catch (e) { /* OneBot 未启动: server 保持 null */ }
      sendResponse({
        pending: forwardingQueue.length,
        deferred: leads.filter(l => l.forwardState === 'deferred').length,
        failed: leads.filter(l => l.forwardState === 'failed').length,
        server
      });
    })();
    return true;
  }

  if (request.type === 'ACCOUNT_CHANGED') {
    currentAccount = request.account;
    console.log('[LeadsLinker] 账号变化:', currentAccount);
    sendResponse({ success: true });
  }

  if (request.type === 'GET_CURRENT_ACCOUNT') {
    sendResponse({ account: currentAccount });
  }

  if (request.type === 'SWITCH_ACCOUNT') {
    currentAccount = request.account;
    console.log('[LeadsLinker] 切换账号:', currentAccount);
    loadAccountConfig(currentAccount);
    sendResponse({ success: true });
  }

  if (request.type === 'UPDATE_ONEBOT_URL') {
    chrome.storage.local.set({ onebotUrl: request.url });
    sendResponse({ success: true });
  }

  if (request.type === 'UPDATE_SYSTEM_NOTIFICATION') {
    chrome.storage.local.set({ systemNotificationEnabled: request.enabled });
    sendResponse({ success: true });
  }

  if (request.type === 'UPDATE_AUTO_REFRESH') {
    chrome.storage.local.set({
      autoRefreshEnabled: request.enabled,
      autoRefreshInterval: request.interval
    });
    sendResponse({ success: true });
  }

  if (request.type === 'UPDATE_KEEP_ALIVE') {
    chrome.storage.local.set({ keepAliveEnabled: request.enabled });
    sendResponse({ success: true });
  }

  if (request.type === 'SHOW_NOTIFICATION') {
    showSystemNotification(request.title, request.body, request.leadId);
    sendResponse({ success: true });
  }

  if (request.type === 'CHECK_LEAD') {
    // 转发前查重: 问插件 leads_check(CSV 历史), 同手机号/微信号今天已转发 → duplicate
    checkLeadDuplicate(request.data || {})
      .then(res => sendResponse(res || { ok: false }))
      .catch(err => sendResponse({ ok: false, error: err.message }));
    return true;
  }

  if (request.type === 'TEST_SEND_LEADS') {
    handleTestSend(request.data, request.count)
      .then(result => sendResponse({ success: true, result }))
      .catch(err => sendResponse({ success: false, error: err.message }));
    return true;
  }

  if (request.type === 'SCHEDULE_TEST_SEND') {
    scheduleTestSend(request.data, request.delaySec)
      .then(() => sendResponse({ ok: true }))
      .catch(err => sendResponse({ ok: false, error: err.message }));
    return true;
  }

  if (request.type === 'CHECK_ONEBOT') {
    checkOnebot(request.url)
      .then(ok => sendResponse({ success: true, online: ok }))
      .catch(err => sendResponse({ success: true, online: false, error: err.message }));
    return true;
  }

  // ⚠️ 不能无条件 return true: 会在第二个监听器(debug通道)响应 DEBUG_* 消息时
  //    引发 "message port closed before a response was received" 竞态 →
  //    content 误判 SW 不可用, 退回页面直连 fetch 被 PNA 拦截, 疯狂报 Failed to fetch。
  return false;
});

function showSystemNotification(title, body, leadId) {
  try {
    chrome.notifications.create(`lead_${leadId}_${Date.now()}`, {
      type: 'basic',
      iconUrl: chrome.runtime.getURL('icons/icon128.png'),
      title: title,
      body: body,
      priority: 2,
      requireInteraction: true
    }, notificationId => {
      if (chrome.runtime.lastError) {
        console.warn('[LeadsLinker] 通知创建失败:', chrome.runtime.lastError.message);
      } else {
        console.log('[LeadsLinker] 通知已显示:', notificationId);
      }
    });
  } catch (e) {
    console.warn('[LeadsLinker] 通知异常:', e.message);
  }
}

async function avatarUrlToBase64(url) {
  if (!url || url.startsWith('data:')) return url || '';

  try {
    const response = await fetch(url);
    if (!response.ok) return '';
    const blob = await response.blob();
    return new Promise((resolve) => {
      const reader = new FileReader();
      reader.onloadend = () => resolve(reader.result);
      reader.onerror = () => resolve('');
      reader.readAsDataURL(blob);
    });
  } catch (e) {
    console.log('[LeadsLinker] 头像获取失败:', e);
    return '';
  }
}

async function processForwardingQueue() {
  if (isForwarding || forwardingQueue.length === 0) return;

  isForwarding = true;

  try {
    while (forwardingQueue.length > 0) {
      const lead = forwardingQueue[0];
      let waitMs = 0;

      try {
        const config = await getConfig();

        // 重试前必须先问服务端查重: 上一轮"失败"可能只是网络/超时抖动,线索其实
        // 已(或正在)发送;盲目重发会让同一条旧线索在群里反复刷屏。
        if (lead.__failCount) {
          const chk = await checkLeadDuplicate({ phone: lead.phone || '', wechat: lead.wechat || '' });
          if (chk && chk.ok && chk.duplicate) {
            console.log('[LeadsLinker] 重试前查重命中(服务端今天已处理), 标记已转发:', lead.name || lead.phone);
            forwardingQueue.shift();
            saveQueue();
            lead.forwardState = 'sent';
            lead.forwardMessage = '服务端今天已处理(重试查重命中), 未重复转发';
            saveLeads();
            clearForwardError();
            continue;
          }
        }

        const result = await forwardToOneBot(lead, config);
        const data = (result && result.data) || {};

        if (data.ok === false) {
          // 明确拒绝(非网络问题, 如参数错误) → 记失败状态, 保留队列低频重试
          throw new Error(data.error || 'send_leads拒绝');
        }

        // 从队列弹出并更新持久化
        forwardingQueue.shift();
        saveQueue();

        if (data.duplicate) {
          // 服务端在途去重:上一次提交仍在后台发送 → 视为已受理,出队不再重试
          lead.forwardState = 'sent';
          lead.forwardMessage = data.message || '服务端处理中(重复请求已忽略)';
          saveLeads();
          clearForwardError();
          continue;
        }

        if (data.queued) {
          // OneBot 排班/阻塞时段暂存 → 服务器端队列负责到点后自动补发,
          // 扩展不再持有(标记 deferred 供弹窗展示), 避免重复入队
          lead.forwardState = 'deferred';
          lead.forwardMessage = data.message || '服务端已暂存, 到可发送时段/重新排班后自动补发';
          console.log('[LeadsLinker] 服务端暂存(等待时段):', lead.name || lead.phone);
        } else {
          lead.forwardState = 'sent';
          lead.forwardMessage = '';
        }
        saveLeads();
        clearForwardError();

      } catch (error) {
        console.error('[LeadsLinker] 转发失败:', error);
        recordForwardError(error);
        lead.__failCount = (lead.__failCount || 0) + 1;
        lead.forwardState = 'failed';
        lead.forwardMessage = ((error && error.message) || String(error)) +
          ' (第' + lead.__failCount + '次, 自动重试中)';
        saveLeads();
        // 不再"3次放弃": OneBot 未启动/时段未开放时一直等待,
        // 指数退避 5s→10s→…封顶 5min, 恢复后自动把队列发完
        waitMs = Math.min(300000, 5000 * Math.max(1, lead.__failCount));
      }

      if (forwardingQueue.length > 0) {
        await new Promise(resolve => setTimeout(resolve, waitMs || 800));
      }
    }
  } finally {
    isForwarding = false;
  }
}

function recordForwardError(error) {
  const message = error instanceof Error ? error.message : String(error);
  chrome.storage.local.set({ lastForwardError: { time: Date.now(), message } });
  console.error('[LeadsLinker] 记录转发错误:', message);
}

function clearForwardError() {
  chrome.storage.local.remove('lastForwardError');
}

async function checkLeadDuplicate(data) {
  const config = await getConfig();
  const url = config.onebotUrl || ONEBOT_URL;
  try {
    const res = await fetch(`${url}/leads_check`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'leads_check', params: data }),
      signal: AbortSignal.timeout(4000)
    });
    if (!res.ok) return { ok: false, error: `HTTP ${res.status}` };
    const json = await res.json();
    return (json && json.data) || { ok: false };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

async function checkOnebot(url) {
  try {
    const res = await fetch(`${url}/status`, { method: 'GET', signal: AbortSignal.timeout(3000) });
    if (!res.ok) return false;
    const data = await res.json();
    return !!(data && (data.online === true || data.good === true));
  } catch (e) {
    return false;
  }
}

async function forwardToOneBot(lead, config) {
  const url = config.onebotUrl || ONEBOT_URL;

  let avatarBase64 = '';
  if (lead.avatar) {
    avatarBase64 = await avatarUrlToBase64(lead.avatar);
  }

  const payload = {
    action: "send_leads",
    params: {
      leads: [{
        id: lead.id,
        timestamp: lead.timestamp,
        account: lead.account,
        name: lead.name || '',
        phone: lead.phone || '',
        wechat: lead.wechat || '',
        note: lead.note || '',
        type: lead.type || '',
        pageUrl: lead.pageUrl || '',
        messageTime: lead.messageTime || '',
        avatar: avatarBase64,
        qrcode: lead.qrcode || ''
      }]
    }
  };

  let response;
  try {
    response = await fetch(`${url}/send_leads`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(8000)
    });
  } catch (e) {
    throw new Error(`无法连接 MagpieBridge(${url}): ${e.message}（请确认已启动）`);
  }

  if (!response.ok) {
    throw new Error(`MagpieBridge响应错误: HTTP ${response.status}`);
  }

  const result = await response.json();
  console.log('[LeadsLinker] 转发成功:', result);
  return result;
}

async function handleTestSend(data, count) {
  const config = await getConfig();
  const url = config.onebotUrl || ONEBOT_URL;

  const leads = [];
  for (let i = 0; i < count; i++) {
    leads.push({
      id: Date.now() + i,
      timestamp: new Date().toISOString(),
      account: currentAccount || '测试账号',
      name: count > 1 ? `${data.name}${i + 1}` : data.name,
      phone: data.phone || '',
      wechat: data.wechat || '',
      note: data.note || '',
      type: data.type || '已留资',
      pageUrl: 'https://life.douyin.com/ (测试)',
      messageTime: new Date().toLocaleString('zh-CN'),
      avatar: '',
      qrcode: data.qrcode || ''
    });
  }

  const payload = {
    action: "test_send_leads",
    params: { leads }
  };

  let response;
  try {
    response = await fetch(`${url}/test_send_leads`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(8000)
    });
  } catch (e) {
    throw new Error(`无法连接 MagpieBridge(${url}): ${e.message}（请确认已启动）`);
  }

  if (!response.ok) {
    throw new Error(`MagpieBridge响应错误: HTTP ${response.status}`);
  }

  const result = await response.json();
  console.log('[LeadsLinker] 测试发送成功:', result);
  return result;
}

// 延时推送测试消息：弹窗点「延时推送」后由 SW 用 chrome.alarms 到点真正发送
// （弹窗关闭也不受影响）。用途：先点延时推送，再立即去 MagpieBridge 点
// 「开始挂起」，验证会话挂回控制台后发送是否正常。
const TEST_SEND_ALARM = 'leads_test_send_delayed';

function scheduleTestSend(data, delaySec) {
  return new Promise((resolve, reject) => {
    const delayMs = Math.max(1, parseInt(delaySec) || 10) * 1000;
    // 存待发送数据 + 创建一次性闹钟。用 storage 存，SW 被唤醒后仍能取到。
    chrome.storage.local.set({ pendingTestSend: data || {} }, () => {
      chrome.alarms.create(TEST_SEND_ALARM, { when: Date.now() + delayMs });
      console.log(`[LeadsLinker] 已安排 ${delaySec}s 后发送测试线索 (count=${data && data.count})`);
      resolve();
    });
  });
}

function getConfig() {
  return new Promise((resolve) => {
    chrome.storage.local.get('config', (result) => {
      resolve(result.config || getDefaultConfig());
    });
  });
}

function getDefaultConfig() {
  return {
    onebotUrl: ONEBOT_URL,
    retryInterval: 5 * 60 * 1000
  };
}

function saveLeads() {
  chrome.storage.local.set({ leads: leads });
}

function saveQueue() {
  try { chrome.storage.local.set({ forwardingQueue: forwardingQueue }); } catch (e) {}
}

function loadAccountConfig(account) {
  chrome.storage.local.get('accountConfigs', result => {
    const accountConfigs = result.accountConfigs || {};
    const config = accountConfigs[account] || {};
    console.log('[LeadsLinker] 加载账号配置:', account, config);
  });
}

chrome.storage.local.get('leads', (result) => {
  if (result.leads) {
    leads = result.leads;
  }
});

// 待转发队列持久化: SW 回收/浏览器重启后恢复, 且自动重新驱动转发
// (OneBot 恢复运行或被阻塞时段解除后, 积压线索自动发出)
chrome.storage.local.get('forwardingQueue', (result) => {
  if (Array.isArray(result.forwardingQueue) && result.forwardingQueue.length) {
    forwardingQueue = result.forwardingQueue;
    console.log('[LeadsLinker] 恢复待转发队列:', forwardingQueue.length, '条, 自动重试');
    processForwardingQueue();
  }
});

chrome.storage.local.get('currentAccount', (result) => {
  if (result.currentAccount) {
    currentAccount = result.currentAccount;
  }
});

// ============================================================
// MV3 Service Worker 保活
// 背景: MV3 SW 空闲约 30s 即被浏览器回收。挂机时 SW 死亡会导致:
//   ① 调试通道轮询停止（平时靠页面 debug-content.js 每 2s 的 DEBUG_PING 保活,
//      但页面未打开/content 脚本未注入时该机制失效）
//   ② 线索查重(CHECK_LEAD)无响应 → maybeSendLead 走"保守跳过" → 漏转发丢客户
// 方案: 用 chrome.alarms 周期性唤醒 SW（权限已在 manifest 声明, 此前从未使用）
// ============================================================
const KEEPALIVE_ALARM = 'leads_sw_keepalive';

try {
  chrome.alarms.create(KEEPALIVE_ALARM, { periodInMinutes: 1 });
} catch (e) {
  console.warn('[LeadsLinker] 创建保活闹钟失败:', e);
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === TEST_SEND_ALARM) {
    // 到点：读取待发送数据，真正发测试线索（弹窗可能已关闭，这里独立完成）。
    chrome.storage.local.get('pendingTestSend', async (r) => {
      const data = r.pendingTestSend;
      if (data) {
        chrome.storage.local.remove('pendingTestSend');
        try {
          const count = parseInt(data.count) || 1;
          await handleTestSend(data, count);
          console.log('[LeadsLinker] 延时测试发送成功');
        } catch (e) {
          console.error('[LeadsLinker] 延时测试发送失败:', e);
          showSystemNotification('LeadsLinker 延时测试发送失败', e.message, Date.now());
        }
      }
    });
    return;
  }
  if (alarm.name !== KEEPALIVE_ALARM) return;
  // SW 已被唤醒: 顶层代码(调试通道已内联 → startPolling)会自动重跑。
  // 关键: 若仍有待转发积压, 每分钟借闹钟重新驱动一次
  // (OneBot 从"未启动/阻塞时段"恢复后, 积压线索自动发出, 不会一直卡着)。
  try {
    if (forwardingQueue.length && !isForwarding) processForwardingQueue();
    if (turbo.on) turboTick();
    chrome.storage.local.get('leads', () => {});
  } catch (e) {
    /* ignore */
  }
});

// ============================================================
// 强劲模式 - SW 侧调度:
//   · 单页面: 关闭多余来客标签页, 只保留一个
//   · 前台:   激活来客标签页 + 聚焦其窗口(纯 chrome API, 无输入模拟)
//   · 刷新:   每 turboRefreshSec 秒 chrome.tabs.reload 整页刷新
//   SW 存活时 20s setTimeout 驱动; SW 休眠后由 1min 闹钟兜底唤醒续跑。
//   (防息屏 wake lock 在 content.js 页面侧实现; 亚秒级扫描参数也在 content.js)
// ============================================================
let turbo = { on: false, refreshSec: 90, foreground: true, singleTab: true };
let turboLastRefresh = Date.now();
let turboTimer = null;

function loadTurbo(cb) {
  chrome.storage.local.get(
    ['turboMode', 'turboRefreshSec', 'turboForeground', 'turboSingleTab'], r => {
      turbo.on = !!r.turboMode;
      turbo.refreshSec = Math.min(600, Math.max(60, parseInt(r.turboRefreshSec) || 90));
      turbo.foreground = r.turboForeground !== false;
      turbo.singleTab = r.turboSingleTab !== false;
      if (cb) cb();
    });
}

async function turboTick() {
  if (!turbo.on) return;
  try {
    const tabs = await chrome.tabs.query({ url: 'https://life.douyin.com/*' });
    if (!tabs.length) return;

    if (turbo.singleTab && tabs.length > 1) {
      const keep = tabs.find(t => t.active) || tabs[tabs.length - 1];
      for (const t of tabs) {
        if (t.id !== keep.id) { try { await chrome.tabs.remove(t.id); } catch (e) {} }
      }
      tabs.length = 0;
      tabs.push(keep);
    }

    const tab = tabs.find(t => t.active) || tabs[0];

    if (turbo.foreground) {
      try { if (!tab.active) await chrome.tabs.update(tab.id, { active: true }); } catch (e) {}
      try { await chrome.windows.update(tab.windowId, { focused: true }); } catch (e) {}
    }

    if (Date.now() - turboLastRefresh >= turbo.refreshSec * 1000) {
      turboLastRefresh = Date.now();
      console.log('[LeadsLinker] 强劲模式: 刷新来客页 tab=' + tab.id);
      try { await chrome.tabs.reload(tab.id, { bypassCache: true }); } catch (e) {}
    }
  } catch (e) { /* noop */ }
}

function turboLoop() {
  clearTimeout(turboTimer);
  if (!turbo.on) { turboTimer = null; return; }
  turboTimer = setTimeout(async () => {
    await turboTick();
    turboLoop();
  }, 20000);
}

loadTurbo(() => {
  if (turbo.on) {
    turboLastRefresh = Date.now();
    turboTick();
    turboLoop();
    console.log('[LeadsLinker] 强劲模式已启用(刷新间隔' + turbo.refreshSec + 's)');
  }
});

chrome.storage.onChanged.addListener((ch, area) => {
  if (area !== 'local') return;
  if (!['turboMode', 'turboRefreshSec', 'turboForeground', 'turboSingleTab'].some(k => k in ch)) return;
  const wasOn = turbo.on;
  loadTurbo(() => {
    if (!turbo.on) { turboLoop(); console.log('[LeadsLinker] 强劲模式已关闭'); return; }
    if (!wasOn) { turboLastRefresh = Date.now(); turboTick(); turboLoop(); }
    console.log('[LeadsLinker] 强劲模式配置更新');
  });
});

// ============================================================
// 全局兜底: 捕获任何意外同步错误, 避免静默失败且便于排查
// ============================================================
self.addEventListener('error', (event) => {
  console.error('[LeadsLinker] SW 未捕获错误:', event.message, event.filename, event.lineno);
});
self.addEventListener('unhandledrejection', (event) => {
  console.error('[LeadsLinker] SW 未处理的 Promise 拒绝:', event.reason && (event.reason.message || event.reason));
});

console.log('[LeadsLinker] Service Worker 启动完成 (background.js 顶层执行完毕)');

// ============================================================
// 调试通道逻辑(原 debug.js)已内联, 消除 importScripts 外部依赖
// ============================================================
// LeadsLinker 调试通道 - 后台中继 (MV3 service worker)
// 架构: AI -> MagpieBridge(:3000 插件中继) -> 本模块轮询领取命令
//       -> 抖音来客 content script 执行 -> 结果回传 -> AI 拉取
// 本文件由 background.js 顶部 importScripts('debug.js') 加载。

(function () {
  'use strict';

  const DEBUG_POLL_INTERVAL = 1500;    // 正常轮询间隔(ms)
  const DEBUG_BACKOFF_INTERVAL = 5000; // OneBot 不可达时的退避间隔(ms)
  const COMMAND_TIMEOUT_MS = 15000;    // 内容脚本执行超时

  let pollTimer = null;
  let inFlight = false;
  let pollInterval = DEBUG_POLL_INTERVAL;
  let swLastPoll = 0;
  let swLastError = '';
  let contentChannelAliveUntil = 0; // content 自轮询在线时, SW 暂停兜底轮询防抢命令

  // SW 侧状态写入 storage, 与 content 自轮询状态(debugChannelState.content)分开
  function saveSwState(patch) {
    try {
      chrome.storage.local.get('debugChannelState', r => {
        const st = Object.assign({}, r.debugChannelState || {});
        st.sw = Object.assign({ active: true }, st.sw || {}, patch, { updated: Date.now() });
        chrome.storage.local.set({ debugChannelState: st });
      });
    } catch (e) { /* noop */ }
  }


  // ------------------------------------------------------------ utils
  function getBaseUrl() {
    return new Promise(resolve => {
      chrome.storage.local.get(['config'], r => {
        const cfg = (r.config || {});
        resolve((cfg.onebotUrl || 'http://127.0.0.1:3000').replace(/\/+$/, ''));
      });
    });
  }

  function isDebugEnabled() {
    return new Promise(resolve => {
      chrome.storage.local.get(['debugEnabled'], r => resolve(r.debugEnabled !== false));
    });
  }

  async function postToServer(action, params, timeoutMs = 3000) {
    const base = await getBaseUrl();
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), timeoutMs);
      const res = await fetch(`${base}/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, params }),
        signal: ctrl.signal
      });
      clearTimeout(timer);
      if (!res.ok) return null;
      const json = await res.json();
      // OneBotResponse 包装: {status, retcode, data}
      return (json && typeof json === 'object' && 'data' in json) ? json.data : json;
    } catch (e) {
      return null;
    }
  }

  function collectState() {
    return new Promise(resolve => {
      chrome.tabs.query({ url: 'https://life.douyin.com/*' }, tabs => {
        const tab = tabs.find(t => t.active) || tabs[0] || null;
        resolve({
          extension_version: chrome.runtime.getManifest().version,
          tab_url: tab ? tab.url : '',
          tab_title: tab ? tab.title : '',
          tab_id: tab ? tab.id : null,
          ts: Date.now()
        });
      });
    });
  }

  function getActiveLaikeTab() {
    return new Promise(resolve => {
      chrome.tabs.query({ url: 'https://life.douyin.com/*' }, tabs => {
        resolve(tabs.find(t => t.active) || tabs[0] || null);
      });
    });
  }

  function dispatchToContent(command) {
    return new Promise(resolve => {
      getActiveLaikeTab().then(tab => {
        if (!tab) return resolve({ ok: false, error: 'no_life_douyin_tab' });
        const timer = setTimeout(() => resolve({ ok: false, error: 'content_timeout' }), COMMAND_TIMEOUT_MS);
        chrome.tabs.sendMessage(tab.id, {
          type: 'DEBUG_COMMAND',
          cmd: command.cmd,
          params: command.params || {}
        }, response => {
          clearTimeout(timer);
          if (chrome.runtime.lastError) {
            resolve({ ok: false, error: chrome.runtime.lastError.message });
          } else {
            resolve(response || { ok: false, error: 'empty_response' });
          }
        });
      });
    });
  }

  async function ensureDebugContent(tabId) {
    try {
      await chrome.scripting.executeScript({ target: { tabId }, files: ['debug-content.js'] });
      return true;
    } catch (e) {
      return false;
    }
  }

  // ------------------------------------------------------------ poll loop
  async function pollOnce() {
    if (inFlight) return;
    if (!(await isDebugEnabled())) return;
    if (Date.now() < contentChannelAliveUntil) { schedulePoll(); return; } // content 自轮询在线 → SW 让位

    const state = await collectState();
    const resp = await postToServer('debug_poll', state, 3000);
    swLastPoll = Date.now();
    if (!resp) {
      swLastError = 'poll_failed(OneBot不可达?)';
      saveSwState({ connected: false, lastPoll: swLastPoll, lastError: swLastError, tab_url: state.tab_url });
      pollInterval = DEBUG_BACKOFF_INTERVAL;
      schedulePoll();
      return;
    }
    swLastError = '';
    saveSwState({ connected: true, lastPoll: swLastPoll, lastError: '', tab_url: state.tab_url });
    pollInterval = DEBUG_POLL_INTERVAL;

    const command = resp.command;
    if (!command) {
      schedulePoll();
      return;
    }

    inFlight = true;
    try {
      let result = await dispatchToContent(command);
      if (!result || result.ok === false) {
        // content script 可能尚未注入(插件刚更新/页面未刷新) → 动态注入后重试一次
        const tab = await getActiveLaikeTab();
        if (tab && await ensureDebugContent(tab.id)) {
          result = await dispatchToContent(command);
        }
      }
      await postToServer('debug_result', {
        cmd_id: command.cmd_id,
        result: result && result.ok ? result.result : null,
        error: result && !result.ok ? (result.error || 'failed') : null
      }, 3000);
    } finally {
      inFlight = false;
      schedulePoll();
    }
  }

  function schedulePoll() {
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = setTimeout(pollOnce, pollInterval);
  }

  function startPolling() {
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = setTimeout(pollOnce, 100);
  }

  // ------------------------------------------------------------ popup API
  async function handleDebugAction(action, params) {
    switch (action) {
      case 'status':
        return postToServer('debug_status', {}, 3000);
      case 'submit':
        return postToServer('debug_query', params, 30000);
      case 'fetch':
        return postToServer('debug_fetch', params, 5000);
      case 'cancel':
        return postToServer('debug_cancel', params, 3000);
      case 'local_status':
        return { enabled: await isDebugEnabled(), inFlight, tab: await collectState() };
      default:
        return { ok: false, error: 'unknown_debug_action' };
    }
  }

  // ------------------------------------------------------------ messages
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (!msg || typeof msg !== 'object') return false;

    if (msg.type === 'DEBUG_PING') {
      // content script 心跳: 唤醒 SW 并立即安排一次轮询
      // (若 content 自轮询在线会另发 DEBUG_CLAIMED, SW 自动让位)
      startPolling();
      sendResponse({ ok: true });
      return false;
    }


    if (msg.type === 'DEBUG_CLAIMED') {
      // content 自轮询成功领取到轮询权 → SW 暂停兜底轮询 6s, 防止两边抢命令
      contentChannelAliveUntil = Date.now() + 6000;
      sendResponse({ ok: true });
      return false;
    }

    if (msg.type === 'DEBUG_HTTP') {
      // content 的 HTTP 代理: SW 扩展上下文发本地请求不受页面 PNA/混合内容限制
      (async () => {
        try {
          const data = await postToServer(String(msg.action || ''), msg.params || {},
            Number(msg.timeoutMs) || 5000);
          if (data === null) {
            sendResponse({ ok: false, error: 'onebot_unreachable' });
          } else {
            sendResponse({ ok: true, data });
          }
        } catch (e) {
          sendResponse({ ok: false, error: String((e && e.message) || e) });
        }
      })();
      return true; // 异步响应
    }

    if (msg.type === 'DEBUG_ACTION') {
      handleDebugAction(msg.action, msg.params || {}).then(res => {
        sendResponse(res || { ok: false, error: 'no_response' });
      });
      return true; // 异步响应
    }
    return false;
  });

  // ------------------------------------------------------------ lifecycle
  // MV3 SW 随时可能被回收: 每次消息/事件都会重新调度轮询;
  // 页面上的 debug-content.js 每 2s 发一次 DEBUG_PING 保持通道活跃。
  startPolling();
})();
