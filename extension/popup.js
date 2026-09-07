// LeadsLinker 弹窗脚本

document.addEventListener('DOMContentLoaded', function() {
  // 关键按钮先绑定：即便后续某个 init 抛错，也不影响"发送测试线索"等核心按钮
  const safe = (fn) => { try { fn() } catch (e) { console.error('[LeadsLinker] init 失败:', e) } };

  document.getElementById('checkOnebotBtn').addEventListener('click', checkOnebotStatus);
  document.getElementById('saveOnebotBtn').addEventListener('click', saveOnebotUrl);
  document.getElementById('saveAutoRefreshBtn').addEventListener('click', saveAutoRefreshSettings);
  document.getElementById('testSendBtn').addEventListener('click', sendTestLeads);
  document.getElementById('testDelaySendBtn').addEventListener('click', sendTestLeadsDelayed);
  document.getElementById('debugSnapshotBtn').addEventListener('click', () => runDebug('快照', { cmd: 'snapshot', timeout: 12 }));
  document.getElementById('debugTestBtn').addEventListener('click', debugTestSelectors);
  document.getElementById('debugFindBtn').addEventListener('click', debugFindText);
  document.getElementById('debugHtmlBtn').addEventListener('click', debugGrabHtml);
  document.getElementById('debugEvalBtn').addEventListener('click', debugEvalJs);
  document.getElementById('clearQueueBtn').addEventListener('click', clearForwardingQueue);
  document.getElementById('retryQueueBtn').addEventListener('click', retryForwardingQueue);

  safe(initTabs);
  safe(loadLeads);
  safe(loadConfig);
  safe(initSystemNotificationToggle);
  safe(initKeepAliveToggle);
  safe(initAutoRefreshToggle);
  safe(initTurboMode);
  safe(initAccountSwitch);
  safe(initDebugPanel);

  safe(checkOnebotStatus);
  safe(loadForwardError);

  setInterval(() => safe(loadLeads), 5000);
  setInterval(() => safe(loadCurrentAccount), 5000);
});

// ---------------------------------------------------------------- OneBot 连通性
function checkOnebotStatus() {
  const url = document.getElementById('onebotUrl').value.trim() || 'http://127.0.0.1:3000';
  const statusEl = document.getElementById('onebotStatus');
  statusEl.textContent = '检测中...';
  statusEl.className = 'test-server-status';

  // 用 8s 超时（OneBot /status 会做一次微信窗口 OCR，偶尔超过 3s），并统一
  // 在错误信息里保留原因，方便排查（避免只看到 [object DOMException]）。
  fetch(`${url}/status`, { method: 'GET', signal: AbortSignal.timeout(8000) })
    .then(res => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    })
    .then(data => {
      if (data && (data.online === true || data.good === true)) {
        statusEl.textContent = 'OneBot 已连接';
        statusEl.className = 'test-server-status online';
      } else {
        statusEl.textContent = 'OneBot 未连接';
        statusEl.className = 'test-server-status offline';
      }
    })
    .catch(err => {
      console.error('[LeadsLinker] 连接检测失败:', err && err.name, err && err.message, err);
      const reason = (err && err.name === 'AbortError')
        ? '超时(8s)，OneBot 可能繁忙'
        : (err && err.message) || String(err);
      statusEl.textContent = 'OneBot 未连接';
      statusEl.className = 'test-server-status offline';
      statusEl.title = reason;
    });
}

function loadForwardError() {
  chrome.storage.local.get('lastForwardError', result => {
    const err = result.lastForwardError;
    const bar = document.getElementById('forwardErrorBar');
    const text = document.getElementById('forwardErrorText');
    if (!bar || !err) return;
    const time = new Date(err.time).toLocaleString('zh-CN');
    text.textContent = `${time}: ${err.message}`;
    text.textContent += ' —— 请确认已启动 MagpieBridge 程序（OneBot 端口 3000）。';
    bar.style.display = 'block';
  });
}

function initTabs() {
  const tabs = document.querySelectorAll('.tab');
  tabs.forEach(tab => {
    tab.addEventListener('click', function() {
      tabs.forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
      this.classList.add('active');
      const tabId = this.getAttribute('data-tab');
      document.getElementById(tabId).classList.add('active');
    });
  });
}

function loadLeads() {
  chrome.runtime.sendMessage({ type: 'GET_LEADS' }, response => {
    if (response && response.leads) {
      renderLeads(response.leads);
    }
  });
  chrome.runtime.sendMessage({ type: 'GET_QUEUE_STATE' }, st => {
    if (!st) return;
    document.getElementById('queueCount').textContent = st.pending;
    const serverQueued = (st.server && st.server.ok && st.server.queued) || 0;
    const serverBlocked = st.server && st.server.blocked;
    const parts = [];
    if (serverQueued) parts.push(`服务端积压 ${serverQueued} 条${serverBlocked ? '(当前时段暂停)' : ''}`);
    else if (st.deferred) parts.push(`已交服务端暂存 ${st.deferred} 条`);
    if (st.failed) parts.push(`本地重试中 ${st.failed} 条`);
    document.getElementById('queueState').textContent = parts.length ? '· ' + parts.join(' · ') : '';
    const hint = document.getElementById('queueHint');
    if (serverQueued > 0) {
      hint.style.display = 'block';
      hint.textContent = serverBlocked
        ? `⏸ 服务端积压 ${serverQueued} 条: 当前处于暂停受理/非允许发送时段。重新排班(放开时段)后 30 秒内自动补发, 或点「立即重试」马上发送。`
        : `服务端积压 ${serverQueued} 条待补发, 可点「立即重试」立即发送。`;
    } else {
      hint.style.display = 'none';
    }
  });
}

function retryForwardingQueue() {
  const btn = document.getElementById('retryQueueBtn');
  btn.disabled = true; btn.textContent = '重试中...';
  chrome.runtime.sendMessage({ type: 'RETRY_QUEUE' }, (res) => {
    setTimeout(() => {
      btn.disabled = false; btn.textContent = '立即重试';
      const s = res && res.server;
      if (s && s.ok && !s.skipped) showToast(`服务端补发 ${s.sent || 0} 条` + (s.duplicated_skipped ? `，跳过已转发 ${s.duplicated_skipped} 条` : '') + (s.remaining ? `，剩 ${s.remaining} 条` : '，队列已空'));
      else if (s && s.skipped) showToast(s.reason === 'blocked_now' ? '仍在暂停/阻塞时段，放开后即可补发' : '服务端暂不可用');
      else if (s && s.ok === false) showToast('连接 OneBot 失败: ' + (s.error || ''));
      else showToast('已触发重试');
      loadLeads();
    }, 1200);
  });
}

function renderLeads(leads) {
  const container = document.getElementById('leadsList');

  if (leads.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <i>📋</i>
        <p>暂无检测到的线索</p>
        <p>请访问 life.douyin.com 开始检测</p>
      </div>
    `;
    return;
  }

  const stateBadge = (lead) => {
    const s = lead.forwardState || 'sent'; // 旧数据默认按已发送显示
    const msg = escapeHtml(lead.forwardMessage || '');
    if (s === 'sent') return '<span style="color:#2e7d32;font-size:11px;">✓ 已发送</span>';
    if (s === 'deferred') return '<span style="color:#e6a23c;font-size:11px;" title="' + msg + '">⏸ 服务端暂存·等待时段自动补发</span>';
    if (s === 'failed') return '<span style="color:#c62828;font-size:11px;" title="' + msg + '">✗ 发送失败·自动重试中</span>';
    if (s === 'pending') return '<span style="color:#909399;font-size:11px;">… 排队中</span>';
    return '';
  };

  const sortedLeads = [...leads].sort((a, b) =>
    new Date(b.timestamp) - new Date(a.timestamp)
  );

  container.innerHTML = sortedLeads.map(lead => `
    <div class="lead-item">
      <div class="time">${formatTime(lead.timestamp)} ${stateBadge(lead)}</div>
      <div class="info">
        ${lead.name ? `<span><span class="label">姓名:</span> ${lead.name}</span>` : ''}
        ${lead.phone ? `<span><span class="label">电话:</span> ${lead.phone}</span>` : ''}
        ${lead.wechat ? `<span><span class="label">微信:</span> ${lead.wechat}</span>` : ''}
        ${lead.note ? `<span><span class="label">备注:</span> ${lead.note}</span>` : ''}
      </div>
      ${lead.forwardState === 'failed' && lead.forwardMessage ? `<div style="color:#c62828;font-size:11px;margin-top:4px;word-break:break-all;">${escapeHtml(lead.forwardMessage)}</div>` : ''}
    </div>
  `).join('');
}

function formatTime(timestamp) {
  const date = new Date(timestamp);
  return date.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit'
  });
}

function loadConfig() {
  chrome.storage.local.get('config', result => {
    const config = result.config || {};

    if (config.onebotUrl) {
      document.getElementById('onebotUrl').value = config.onebotUrl;
    }
  });
}

function saveOnebotUrl() {
  const url = document.getElementById('onebotUrl').value.trim();
  saveConfig({ onebotUrl: url });
  chrome.runtime.sendMessage({ type: 'UPDATE_ONEBOT_URL', url: url });
  showSaveSuccess('OneBot连接配置已保存');
}

function saveConfig(newConfig) {
  chrome.storage.local.get('config', result => {
    const config = result.config || {};
    const updatedConfig = { ...config, ...newConfig };
    chrome.storage.local.set({ config: updatedConfig });
  });
}

function showSaveSuccess(message) {
  const notification = document.createElement('div');
  notification.textContent = message;
  notification.style.cssText = `
    position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
    background: #4CAF50; color: white; padding: 10px 20px; border-radius: 4px; z-index: 1000;
  `;
  document.body.appendChild(notification);
  setTimeout(() => notification.remove(), 2000);
}

function initSystemNotificationToggle() {
  const toggle = document.getElementById('systemNotificationToggle');
  chrome.storage.local.get('systemNotificationEnabled', result => {
    toggle.checked = result.systemNotificationEnabled || false;
  });
  toggle.addEventListener('change', function() {
    const enabled = this.checked;
    chrome.storage.local.set({ systemNotificationEnabled: enabled });
    chrome.runtime.sendMessage({ type: 'UPDATE_SYSTEM_NOTIFICATION', enabled });
    showSaveSuccess(enabled ? '系统通知已开启' : '系统通知已关闭');
  });
}

function initKeepAliveToggle() {
  const toggle = document.getElementById('keepAliveToggle');
  chrome.storage.local.get('keepAliveEnabled', result => {
    toggle.checked = result.keepAliveEnabled || false;
    if (toggle.checked) startKeepAlive();
  });
  toggle.addEventListener('change', function() {
    const enabled = this.checked;
    chrome.storage.local.set({ keepAliveEnabled: enabled });
    chrome.runtime.sendMessage({ type: 'UPDATE_KEEP_ALIVE', enabled });
    if (enabled) startKeepAlive(); else stopKeepAlive();
    showSaveSuccess(enabled ? '保持活跃已开启' : '保持活跃已关闭');
  });
}

let keepAliveTimer = null;

function startKeepAlive() {
  if (keepAliveTimer) return;
  keepAliveTimer = setInterval(() => {
    chrome.tabs.query({ url: 'https://life.douyin.com/*' }, tabs => {
      tabs.forEach(tab => {
        chrome.tabs.sendMessage(tab.id, { type: 'KEEP_ALIVE' });
      });
    });
  }, 30000);
}

function stopKeepAlive() {
  if (keepAliveTimer) { clearInterval(keepAliveTimer); keepAliveTimer = null; }
}

function initAutoRefreshToggle() {
  const toggle = document.getElementById('autoRefreshToggle');
  const configDiv = document.getElementById('autoRefreshConfig');
  const intervalInput = document.getElementById('autoRefreshInterval');

  chrome.storage.local.get(['autoRefreshEnabled', 'autoRefreshInterval'], result => {
    toggle.checked = result.autoRefreshEnabled || false;
    intervalInput.value = result.autoRefreshInterval || 10;
    configDiv.style.display = toggle.checked ? 'block' : 'none';
    if (toggle.checked) startAutoRefresh(intervalInput.value);
  });

  toggle.addEventListener('change', function() {
    const enabled = this.checked;
    const interval = parseInt(intervalInput.value) || 10;
    chrome.storage.local.set({ autoRefreshEnabled: enabled, autoRefreshInterval: interval });
    chrome.runtime.sendMessage({ type: 'UPDATE_AUTO_REFRESH', enabled, interval });
    configDiv.style.display = enabled ? 'block' : 'none';
    if (enabled) startAutoRefresh(interval); else stopAutoRefresh();
    showSaveSuccess(enabled ? `自动刷新已开启，间隔 ${interval} 分钟` : '自动刷新已关闭');
  });
}

let autoRefreshTimer = null;

function startAutoRefresh(intervalMinutes) {
  stopAutoRefresh();
  const intervalMs = intervalMinutes * 60 * 1000;
  autoRefreshTimer = setInterval(() => {
    chrome.tabs.query({ url: 'https://life.douyin.com/*' }, tabs => {
      tabs.forEach(tab => chrome.tabs.reload(tab.id));
    });
  }, intervalMs);
}

function stopAutoRefresh() {
  if (autoRefreshTimer) { clearInterval(autoRefreshTimer); autoRefreshTimer = null; }
}

function saveAutoRefreshSettings() {
  const interval = parseInt(document.getElementById('autoRefreshInterval').value) || 10;
  chrome.storage.local.set({ autoRefreshInterval: interval });
  chrome.storage.local.get('autoRefreshEnabled', result => {
    if (result.autoRefreshEnabled) startAutoRefresh(interval);
  });
  showSaveSuccess(`刷新间隔已设置为 ${interval} 分钟`);
}

// =================================================================
// 强劲模式: 前台+防息屏+亚秒扫描+周期刷新+单页面(纯 chrome API/DOM, 不碰输入设备)
// =================================================================
function initTurboMode() {
  const toggle = document.getElementById('turboToggle');
  const configDiv = document.getElementById('turboConfig');
  const refreshInput = document.getElementById('turboRefreshSec');
  const fgCheck = document.getElementById('turboForeground');
  const singleCheck = document.getElementById('turboSingleTab');

  chrome.storage.local.get(['turboMode', 'turboRefreshSec', 'turboForeground', 'turboSingleTab'], r => {
    toggle.checked = !!r.turboMode;
    refreshInput.value = r.turboRefreshSec || 90;
    fgCheck.checked = r.turboForeground !== false;
    singleCheck.checked = r.turboSingleTab !== false;
    configDiv.style.display = toggle.checked ? 'block' : 'none';
  });

  toggle.addEventListener('change', () => {
    chrome.storage.local.set({ turboMode: toggle.checked });
    configDiv.style.display = toggle.checked ? 'block' : 'none';
    showToast(toggle.checked ? '⚡ 强劲模式已开启' : '强劲模式已关闭');
  });

  document.getElementById('saveTurboBtn').addEventListener('click', () => {
    const sec = Math.min(600, Math.max(60, parseInt(refreshInput.value) || 90));
    refreshInput.value = sec;
    chrome.storage.local.set({
      turboMode: toggle.checked,
      turboRefreshSec: sec,
      turboForeground: fgCheck.checked,
      turboSingleTab: singleCheck.checked
    });
    showToast('强劲模式设置已保存');
  });
}

function initAccountSwitch() {
  const accountSelect = document.getElementById('accountSelect');
  chrome.storage.local.get('accounts', result => {
    const accounts = result.accounts || [];
    renderAccountOptions(accounts);
  });
  accountSelect.addEventListener('change', function() {
    if (this.value) switchAccount(this.value);
  });
  loadCurrentAccount();
}

function renderAccountOptions(accounts) {
  const accountSelect = document.getElementById('accountSelect');
  const currentValue = accountSelect.value;
  accountSelect.innerHTML = '<option value="">切换账号</option>';
  accounts.forEach(account => {
    const option = document.createElement('option');
    option.value = account;
    option.textContent = account;
    if (account === currentValue) option.selected = true;
    accountSelect.appendChild(option);
  });
}

function loadCurrentAccount() {
  chrome.runtime.sendMessage({ type: 'GET_CURRENT_ACCOUNT' }, response => {
    if (response && response.account) {
      document.getElementById('currentAccount').textContent = response.account;
      chrome.storage.local.get('accounts', result => {
        const accounts = result.accounts || [];
        if (!accounts.includes(response.account)) {
          accounts.push(response.account);
          chrome.storage.local.set({ accounts });
          renderAccountOptions(accounts);
        }
      });
    } else {
      document.getElementById('currentAccount').textContent = '未检测到';
    }
  });
}

function switchAccount(account) {
  chrome.runtime.sendMessage({ type: 'SWITCH_ACCOUNT', account }, response => {
    if (response && response.success) {
      document.getElementById('currentAccount').textContent = account;
      showSaveSuccess(`已切换到账号: ${account}`);
      loadConfig();
    }
  });
}

function sendTestLeads() {
  try {
    const name = document.getElementById('testName').value.trim();
    const phone = document.getElementById('testPhone').value.trim();
    const wechat = document.getElementById('testWechat').value.trim();
    const note = document.getElementById('testNote').value.trim();
    const type = document.getElementById('testType').value;
    const count = parseInt(document.getElementById('testCount').value) || 1;
    const url = document.getElementById('onebotUrl').value.trim() || 'http://127.0.0.1:3000';

    const btn = document.getElementById('testSendBtn');
    const resultDiv = document.getElementById('testResult');
    btn.disabled = true;
    btn.textContent = '发送中...';
    resultDiv.style.display = 'block';
    resultDiv.style.color = '#666';
    resultDiv.textContent = `正在发送 ${count} 条测试线索...`;

    const leads = [];
    for (let i = 0; i < count; i++) {
      leads.push({
        id: Date.now() + i,
        timestamp: new Date().toISOString(),
        account: '测试账号',
        name: count > 1 ? `${name}${i + 1}` : name,
        phone, wechat, note, type,
        pageUrl: 'https://life.douyin.com/ (测试)',
        messageTime: new Date().toLocaleString('zh-CN'),
        avatar: ''
      });
    }

    fetch(`${url}/test_send_leads`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'test_send_leads', params: { leads } }),
      signal: AbortSignal.timeout(8000)
    })
      .then(res => res.json().then(data => ({ ok: res.ok, status: res.status, data })))
      .then(({ ok, status, data }) => {
        btn.disabled = false;
        btn.textContent = '发送测试线索';
        if (!ok) throw new Error(`HTTP ${status}`);
        // show the server's own message (e.g. 未配置目标群，未发送)
        const serverMsg = (data && data.data && data.data.message) || '';
        resultDiv.style.color = '#2e7d32';
        resultDiv.textContent = serverMsg
          ? `已收到响应：${serverMsg}`
          : `成功发送 ${count} 条测试线索`;
        if (serverMsg) {
          resultDiv.style.color = '#e6a23c';
          resultDiv.textContent += '（请到配置页设置目标群）';
        }
      })
      .catch(err => {
        console.error('[LeadsLinker] 测试发送失败:', err);
        btn.disabled = false;
        btn.textContent = '发送测试线索';
        resultDiv.style.color = '#c62828';
        resultDiv.textContent = `发送失败: ${err.message}（请确认 MagpieBridge 已启动）`;
      });
  } catch (e) {
    console.error('[LeadsLinker] 测试发送异常:', e);
    const resultDiv = document.getElementById('testResult');
    if (resultDiv) { resultDiv.style.display = 'block'; resultDiv.style.color = '#c62828'; resultDiv.textContent = '测试发送异常：' + e.message; }
  }
}

// 延时推送测试消息：先点「延时推送」，再立刻去 MagpieBridge 软件点「开始挂起」，
// 到点后由 background 的 chrome.alarms 负责真正发送（弹窗关闭也不受影响）。
// 用途：验证「会话挂回控制台后」发送是否正常（远程桌面断开/息屏场景）。
function sendTestLeadsDelayed() {
  const resultDiv = document.getElementById('testResult');
  const btn = document.getElementById('testDelaySendBtn');
  try {
    const delaySec = parseInt(document.getElementById('testDelaySec').value) || 0;
    if (!delaySec || delaySec < 1) {
      resultDiv.style.display = 'block';
      resultDiv.style.color = '#c62828';
      resultDiv.textContent = '请设置延时秒数（>=1）';
      return;
    }
    const data = {
      name: document.getElementById('testName').value.trim(),
      phone: document.getElementById('testPhone').value.trim(),
      wechat: document.getElementById('testWechat').value.trim(),
      note: document.getElementById('testNote').value.trim(),
      type: document.getElementById('testType').value,
      count: parseInt(document.getElementById('testCount').value) || 1
    };
    btn.disabled = true;
    resultDiv.style.display = 'block';
    resultDiv.style.color = '#e6a23c';
    resultDiv.textContent = `正在安排 ${delaySec} 秒后发送 ${data.count} 条测试线索...`;

    chrome.runtime.sendMessage({ type: 'SCHEDULE_TEST_SEND', data, delaySec }, res => {
      if (res && res.ok) {
        resultDiv.style.color = '#2e7d32';
        resultDiv.textContent = `已安排 ${delaySec} 秒后发送。请现在去软件点「开始挂起」，等待到点后发送。`;
        // 倒计时提示
        let remain = delaySec;
        const timer = setInterval(() => {
          remain--;
          if (remain > 0) {
            resultDiv.textContent = `已安排发送，剩余 ${remain} 秒。请现在去软件点「开始挂起」。`;
          } else {
            clearInterval(timer);
            resultDiv.textContent = `已到点发送 ${data.count} 条测试线索，等待结果...`;
          }
        }, 1000);
      } else {
        resultDiv.style.color = '#c62828';
        resultDiv.textContent = '安排延时发送失败: ' + (res && res.error ? res.error : '无法调度');
      }
      btn.disabled = false;
    });
  } catch (e) {
    console.error('[LeadsLinker] 延时发送异常:', e);
    if (resultDiv) { resultDiv.style.display = 'block'; resultDiv.style.color = '#c62828'; resultDiv.textContent = '延时发送异常：' + e.message; }
    if (btn) btn.disabled = false;
  }
}

// =================================================================
// 通用 toast 提示（调试面板等复用）
// =================================================================
function showToast(msg) {
  const toast = document.getElementById('diagToast');
  if (!toast) { console.log('[LeadsLinker] toast:', msg); return; }
  toast.textContent = msg;
  toast.style.display = 'block';
  setTimeout(() => toast.style.display = 'none', 2000);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

// =================================================================
// 调试通道面板：通过 background 的 DEBUG_ACTION 与 OneBot(3000) 中继交互
// =================================================================
function initDebugPanel() {
  const toggle = document.getElementById('debugToggle');
  chrome.storage.local.get('debugEnabled', r => {
    toggle.checked = r.debugEnabled !== false;
  });
  toggle.addEventListener('change', () => {
    chrome.storage.local.set({ debugEnabled: toggle.checked });
    showToast(toggle.checked ? '调试通道已开启' : '调试通道已关闭');
  });

  refreshDebugStatus();
  setInterval(refreshDebugStatus, 5000);
}

function refreshDebugStatus() {
  chrome.storage.local.get('debugChannelState', st => {
    const chan = st.debugChannelState || {};
    const c = chan.content || {};
    const fmtAgo = ts => {
      if (!ts) return '从未';
      const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
      return s < 60 ? s + 's前' : Math.round(s / 60) + 'min前';
    };
    let chanHtml = '<br>content轮询: <b style="color:' + (c.connected ? '#2e7d32' : '#c62828') + ';">' +
      (c.connected ? '在线' : '离线') + '</b> (' + fmtAgo(c.lastOk) + ')';
    if (c.lastError) chanHtml += ' <span style="color:#c62828;">' + escapeHtml(String(c.lastError).slice(0, 60)) + '</span>';
    const sw = chan.sw || {};
    if (Object.keys(sw).length) {
      chanHtml += '<br>SW兜底轮询: <b>' + (sw.connected ? '在线' : '离线') + '</b> (' + fmtAgo(sw.lastPoll) + ')';
      if (sw.lastError) chanHtml += ' <span style="color:#c62828;">' + escapeHtml(String(sw.lastError).slice(0, 60)) + '</span>';
    }

    chrome.runtime.sendMessage({ type: 'DEBUG_ACTION', action: 'status', params: {} }, res => {
      const line = document.getElementById('debugStatusLine');
      if (!line) return;
      if (res && res.ok) {
        let html = '调试通道: <b>' + (res.enabled ? '开' : '关') + '</b> | OneBot中继看到扩展: <b style="color:' +
          (res.extension_connected ? '#2e7d32' : '#c62828') + ';">' +
          (res.extension_connected ? '在线' : '离线') + '</b> | 队列: ' + res.pending;
        if (res.extension && res.extension.url) {
          html += '<br>中继心跳页面: ' + escapeHtml(String(res.extension.url).slice(0, 70)) +
            ' [' + escapeHtml(String(res.extension.channel || 'sw')) + ']';
        }
        html += chanHtml;
        if (!res.extension_connected) {
          html += '<br><span style="color:#c62828;">扩展未上报心跳：确认 life.douyin.com 页面已打开、扩展已重新加载(v1.2+)、OneBot(3000) 已启动。</span>';
        }
        line.innerHTML = html;
      } else {
        line.innerHTML = '<span style="color:#c62828;">无法连接 OneBot(3000)：请确认 MagpieBridge 已启动、插件 leads_forwarder 已加载(v4.0+)。</span>' + chanHtml;
      }
    });
  });
}

function debugInputValue() {
  return document.getElementById('debugInput').value.trim();
}

function runDebug(label, params) {
  const out = document.getElementById('debugOutput');
  out.textContent = '⏳ ' + label + ' ...';
  chrome.runtime.sendMessage({ type: 'DEBUG_ACTION', action: 'submit', params }, res => {
    if (!res) {
      out.textContent = label + '\n错误: 无响应（请检查 OneBot 连接）';
      return;
    }
    if (res.status === 'timeout') {
      out.textContent = label + '\n[timeout] 命令已入队但扩展未及时回传 (cmd_id=' + res.cmd_id + ')';
      return;
    }
    out.textContent = label + '\n' + JSON.stringify(res, null, 2);
  });
}

function debugTestSelectors() {
  const input = debugInputValue();
  if (!input) { showToast('请输入选择器（逗号分隔）'); return; }
  const selectors = input.split(',').map(s => s.trim()).filter(Boolean);
  runDebug('测试选择器 (' + selectors.length + '个)', {
    cmd: 'test_selectors',
    params: { selectors: selectors },
    timeout: 12
  });
}

function debugFindText() {
  const input = debugInputValue();
  if (!input) { showToast('请输入要查找的文本'); return; }
  runDebug('按文本查找: ' + input, {
    cmd: 'find_text',
    params: { text: input, max: 10 },
    timeout: 12
  });
}

function debugGrabHtml() {
  const input = debugInputValue() || 'body';
  runDebug('抓取HTML: ' + input, {
    cmd: 'html',
    params: { selector: input, limit: 3, maxLen: 4000 },
    timeout: 12
  });
}

function debugEvalJs() {
  const code = debugInputValue();
  if (!code) { showToast('请输入 JS 代码'); return; }
  runDebug('执行JS', {
    cmd: 'eval_code',
    params: { code: code },
    timeout: 12
  });
}

// =================================================================
// 清空待转发队列
// =================================================================
function clearForwardingQueue() {
  const btn = document.getElementById('clearQueueBtn');
  const count = parseInt(document.getElementById('queueCount').textContent) || 0;

  if (btn.dataset.armed === '1') {
    // 二次点击确认后真正清空
    chrome.runtime.sendMessage({ type: 'CLEAR_QUEUE' }, res => {
      btn.dataset.armed = '';
      btn.textContent = '清空待转发';
      if (res && res.success) {
        showToast(`已清空 ${res.cleared} 条待转发`);
        loadLeads(); // 刷新列表与计数
      } else {
        showToast('清空失败，请重试');
      }
    });
    return;
  }

  // 第一次点击: 武装确认状态
  btn.dataset.armed = '1';
  btn.textContent = count > 0 ? `确认清空 ${count} 条?` : '确认清空?';
  setTimeout(() => {
    btn.dataset.armed = '';
    btn.textContent = '清空待转发';
  }, 3000);
}
