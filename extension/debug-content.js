// LeadsLinker 调试通道 - 内容脚本命令执行器 + 自轮询循环
// 在抖音来客页面内执行 AI 下发的调试命令，返回真实 DOM 信息。
// 轮询架构(v1.2): 本脚本自己驱动轮询, HTTP 一律经 service worker 代理
//   (DEBUG_HTTP)，SW 只做两件事: 代发 fetch + 兜底轮询(content 心跳不新鲜时)。
//   所有失败写入 chrome.storage.local.debugChannelState 供弹窗诊断。
// 命令列表:
//   page_info         页面基础信息
//   snapshot          完整快照(账号候选+标签+消息项+线索标记)
//   account_candidates 账号候选元素
//   current_account_guess 当前账号启发式猜测
//   tabs              标签页结构
//   message_items     消息/会话列表项
//   markers           已留资/广告源/经营源 标记定位
//   test_selectors    批量测试选择器
//   search_text       正则搜索文本节点(预编译, CSP 安全; 找手机号/微信号)
//   conversation_items 会话列表项完整结构转储(class/叶子文本/图/未读角标)
//   window_messages   消息窗口结构转储(方向/时间gap/卡片叶子结构)
//   content_health    content.js 健康自检(版本/循环活性/最近跳过原因/报错)
//   find_text         按文本找元素
//   eval_code         执行任意 JS (注意: 来客页 CSP 禁 eval, 该命令会被拦, 优先用上面预编译命令)
//   walk              DOM 结构概览(标签/class 统计)
//   html              抓取指定选择器 outerHTML
//   capture           整页 HTML
//   network           网络/资源记录
//   mutation_test     MutationObserver 动态加载观察

(function () {
  'use strict';

  // 防重复注入: manifest content_scripts + SW executeScript 兜底可能叠加,
  // 双实例会导致双轮询抢命令 / 双 sendResponse 冲突。
  if (window.__leadslinkerDebugLoaded) return;
  window.__leadslinkerDebugLoaded = true;
  // ------------------------------------------------------------ 自轮询通道
  // v1.2 起 content 自己轮询领取命令(不再依赖 SW 的 setTimeout 循环):
  //   host_permissions 已含 http://127.0.0.1:*/* → content 内 fetch 不受
  //   页面 CSP/CORS 限制。失败写 debugChannelState 供弹窗诊断。
  const DBG_DEFAULT_BASE = 'http://127.0.0.1:3000';
  const DBG_POLL_MS = 2000;
  const DBG_BACKOFF_MS = 6000;
  let dbgInFlight = false;
  let dbgTimer = null;
  let dbgFailCount = 0;

  function dbgSaveState(patch) {
    try {
      chrome.storage.local.get('debugChannelState', r => {
        const st = Object.assign({}, r.debugChannelState || {});
        st.content = Object.assign({ active: true }, st.content || {}, patch, {
          updated: Date.now()
        });
        chrome.storage.local.set({ debugChannelState: st });
      });
    } catch (e) { /* 扩展上下文失效: 忽略 */ }
  }

  function dbgBaseUrl() {
    return new Promise(resolve => {
      try {
        chrome.storage.local.get('config', r => {
          const cfg = r.config || {};
          resolve((cfg.onebotUrl || DBG_DEFAULT_BASE).replace(/\/+$/, ''));
        });
      } catch (e) { resolve(DBG_DEFAULT_BASE); }
    });
  }

  function dbgEnabled() {
    return new Promise(resolve => {
      try {
        chrome.storage.local.get('debugEnabled', r => resolve(r.debugEnabled !== false));
      } catch (e) { resolve(true); }
    });
  }

  // HTTP 通道(两级): ① SW 代理(Debug_HTTP)——扩展上下文不受页面 PNA/混合内容限制
  //                  ② 直连 fetch —— SW 不在线时兜底(https 页内可能被 Private-Network-Access 拦截)
  function dbgPostViaSw(base, action, params, timeoutMs) {
    return new Promise(resolve => {
      let done = false;
      const timer = setTimeout(() => {
        if (!done) { done = true; resolve({ __sw_fail: 'sw_timeout' }); }
      }, (timeoutMs || 4000) + 2000);
      try {
        if (!(chrome.runtime && chrome.runtime.id)) {
          // 扩展被重新加载 → 本脚本已孤儿化, 任何 chrome API 都不可用
          resolve({ __sw_fail: 'extension_reloaded_refresh_page' });
          return;
        }
        chrome.runtime.sendMessage({ type: 'DEBUG_HTTP', base, action, params }, res => {
          if (done) return;
          done = true; clearTimeout(timer);
          const le = chrome.runtime.lastError;
          if (le && /context invalidated|Extension context/i.test(le.message || '')) {
            resolve({ __sw_fail: 'extension_reloaded_refresh_page' });
            return;
          }
          if (le || !res || res.ok !== true) {
            resolve({ __sw_fail: (le && le.message) || (res && res.error) || 'sw_no_response' });
          } else {
            resolve(res.data);
          }
        });
      } catch (e) {
        const msg = String((e && e.message) || e);
        if (!done) { done = true; clearTimeout(timer); resolve({ __sw_fail: /Extension context|disconnected/i.test(msg) ? 'extension_reloaded_refresh_page' : msg }); }
      }
    });
  }

  async function dbgPost(base, action, params, timeoutMs) {
    const viaSw = await dbgPostViaSw(base, action, params, timeoutMs);
    if (viaSw && viaSw.__sw_fail === 'extension_reloaded_refresh_page') {
      const err = new Error('extension_reloaded_refresh_page');
      err.ctxDead = true;
      throw err;
    }
    if (viaSw && viaSw.__sw_fail) {
      // SW 临时不可达(未启动/忙) → 页面直连兜底一次
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), timeoutMs || 4000);
      try {
        const res = await fetch(`${base}/${action}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action, params }),
          signal: ctrl.signal
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = await res.json();
        return (json && typeof json === 'object' && 'data' in json) ? json.data : json;
      } finally {
        clearTimeout(timer);
      }
    }
    return viaSw;
  }

  let dbgCtxDead = false; // 扩展重载 → content 脚本被孤立, 只能刷新页面恢复
  async function dbgPollOnce() {
    if (dbgCtxDead) return;
    if (dbgInFlight) return;
    if (!(await dbgEnabled())) { dbgSchedule(DBG_BACKOFF_MS); return; }
    // 后台标签页不抢命令(交给用户正在看的 tab / SW 兜底轮询)
    if (document.visibilityState !== 'visible') { dbgSchedule(DBG_POLL_MS * 3); return; }
    dbgInFlight = true;
    try {
      const base = await dbgBaseUrl();
      const state = {
        channel: 'content',
        tab_url: location.href,
        tab_title: document.title,
        extension_version: (function () {
          try { return chrome.runtime.getManifest().version; } catch (e) { return ''; }
        })(),
        ts: Date.now()
      };
      const resp = await dbgPost(base, 'debug_poll', state, 4000);
      if (!resp || resp.ok !== true) throw new Error('bad_poll_response');
      dbgFailCount = 0;
      dbgSaveState({ connected: true, base, lastOk: state.ts, lastError: '' });
      // 通知 SW: content 通道活着, SW 暂停自己的兜底轮询(防止两边抢命令)
      try { chrome.runtime.sendMessage({ type: 'DEBUG_CLAIMED' }); } catch (e) { /* noop */ }
      const command = resp.command;
      if (command) {
        let result;
        try {
          const handler = commands[command.cmd];
          if (!handler) {
            result = { ok: false, error: `unknown_command: ${command.cmd}` };
          } else {
            const r = handler(command.params || {});
            const resolved = (r && typeof r.then === 'function') ? await r : r;
            result = { ok: true, cmd: command.cmd, result: resolved };
          }
        } catch (e) {
          result = { ok: false, error: String((e && e.message) || e) };
        }
        try {
          await dbgPost(base, 'debug_result', {
            cmd_id: command.cmd_id,
            result: result.ok ? result.result : null,
            error: result.ok ? null : result.error
          }, 5000);
        } catch (e) { dbgSaveState({ lastError: 'report_fail: ' + String((e && e.message) || e) }); }
      }
    } catch (e) {
      if (e && e.ctxDead) {
        // 扩展已重新加载 → 本 content 脚本被孤立, chrome API 全失效, 只能刷新页面。
        // 停止轮询, 不再刷屏(一条清晰提示足够)。
        dbgCtxDead = true;
        if (dbgTimer) clearTimeout(dbgTimer);
        console.warn('[LeadsLinker][dbg] 扩展已重新加载, 调试通道 content 端已失联 → 请刷新本页面(F5)以恢复。');
        dbgSaveState({ connected: false, lastError: 'extension_reloaded_refresh_page' });
        return;
      }
      dbgFailCount++;
      const msg = String((e && e.message) || e);
      console.warn('[LeadsLinker][dbg] 轮询失败(连续' + dbgFailCount + '次):', msg,
        '(若为 Failed to fetch: 检查 OneBot(3000) 是否启动、manifest host_permissions 是否含 http://127.0.0.1:*/*)');
      dbgSaveState({
        connected: false,
        lastError: msg,
        failCount: dbgFailCount,
        base: ''
      });
    } finally {
      dbgInFlight = false;
      dbgSchedule(dbgFailCount > 0 ? DBG_BACKOFF_MS : DBG_POLL_MS + Math.floor(Math.random() * 500));
    }
  }

  function dbgSchedule(ms) {
    if (dbgTimer) clearTimeout(dbgTimer);
    dbgTimer = setTimeout(dbgPollOnce, ms);
  }

  function dbgStart() {
    console.log('[LeadsLinker][dbg] content 自轮询已启动 (target=' + DBG_DEFAULT_BASE + ')');
    // 随机抖动: 多个来客 tab 同时打开时错开轮询, 减少命令被别的 tab 抢走
    dbgSchedule(300 + Math.floor(Math.random() * 1200));
  }

  // 心跳: 保持后台 SW 活跃(SW 兜底轮询用) (每 2s)
  setInterval(() => {
    try { chrome.runtime.sendMessage({ type: 'DEBUG_PING' }); } catch (e) { /* noop */ }
  }, 2000);


  // ------------------------------------------------------------ helpers
  function elPath(el, max = 4) {
    if (!el || el.nodeType !== 1) return '';
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < max) {
      let seg = node.tagName.toLowerCase();
      if (node.id) seg += '#' + node.id;
      const cls = (typeof node.className === 'string' && node.className.trim())
        ? node.className.trim().split(/\s+/).slice(0, 3) : [];
      if (cls.length) seg += '.' + cls.join('.');
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(c => c.tagName === node.tagName);
        if (siblings.length > 1) seg += `:nth-of-type(${siblings.indexOf(node) + 1})`;
      }
      parts.unshift(seg);
      node = parent;
    }
    return parts.join(' > ');
  }

  function cleanText(s) { return (s || '').replace(/\s+/g, ' ').trim(); }

  function styleInfo(el) {
    try {
      const cs = window.getComputedStyle(el);
      return {
        display: cs.display,
        color: cs.color,
        bg: cs.backgroundColor,
        fontSize: cs.fontSize,
        fontWeight: cs.fontWeight,
        visible: el.offsetParent !== null
      };
    } catch (e) { return {}; }
  }

  function rectInfo(el) {
    try {
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
    } catch (e) { return {}; }
  }

  function describe(el, maxText = 120) {
    if (!el || el.nodeType !== 1) return null;
    return {
      tag: el.tagName.toLowerCase(),
      id: el.id || '',
      classes: (typeof el.className === 'string' ? el.className.trim() : ''),
      path: elPath(el, 6),
      text: cleanText(el.textContent).substring(0, maxText),
      style: styleInfo(el),
      rect: rectInfo(el),
      childCount: el.children.length
    };
  }

  function safeJson(v) {
    try { return JSON.parse(JSON.stringify(v)); } catch (e) { return String(v); }
  }

  // ------------------------------------------------------------ commands
  const commands = {

    page_info() {
      return {
        url: location.href,
        title: document.title,
        readyState: document.readyState,
        userAgent: navigator.userAgent,
        referrer: document.referrer,
        lang: document.documentElement.lang,
        viewport: { w: innerWidth, h: innerHeight, dpr: devicePixelRatio },
        screen: { w: screen.width, h: screen.height },
        cookieCount: document.cookie.split(';').filter(Boolean).length,
        localKeys: (() => { try { return Object.keys(localStorage); } catch (e) { return []; } })(),
        sessionKeys: (() => { try { return Object.keys(sessionStorage); } catch (e) { return []; } })(),
        links: Array.from(document.querySelectorAll('a[href]'))
          .map(a => a.href).filter(h => h.includes('life.douyin.com')).slice(0, 20),
        iframes: Array.from(document.querySelectorAll('iframe')).slice(0, 20).map(f => ({
          src: f.src, id: f.id, cls: typeof f.className === 'string' ? f.className : ''
        }))
      };
    },

    account_candidates() {
      const selectors = [
        '.ellipsis-uVbhQc',
        '[class*="ellipsis"]',
        '[class*="account"]',
        '[class*="Account"]',
        '[class*="merchant"]',
        '[class*="Merchant"]',
        '[class*="shop"]',
        '[class*="Shop"]',
        '[class*="store"]',
        '[class*="name"]',
        '[class*="user"]',
        '[class*="User"]',
        '[class*="header"]',
        '[class*="Header"]',
        '.user-name', '.account-name', '.merchant-name', '.shop-name'
      ];
      const results = [];
      const seen = new Set();
      for (const sel of selectors) {
        let els;
        try { els = document.querySelectorAll(sel); } catch (e) { continue; }
        for (const el of Array.from(els).slice(0, 3)) {
          const text = cleanText(el.textContent);
          if (!text || text.length > 60) continue;
          const key = el.tagName + '.' + (typeof el.className === 'string' ? el.className : '') + '|' + text;
          if (seen.has(key)) continue;
          seen.add(key);
          results.push({ selector: sel, ...describe(el, 60) });
        }
      }
      // 顶部固定区域里的短文本叶子元素
      const top = document.querySelector('[class*="header"], [class*="Header"], [class*="layout"], [class*="top"], nav, aside');
      if (top) {
        const leafs = Array.from(top.querySelectorAll('span, div, p, a, h1, h2, h3'))
          .filter(el => {
            const t = cleanText(el.textContent);
            return t && t.length <= 30 && el.children.length === 0;
          });
        leafs.slice(0, 30).forEach(el => {
          const text = cleanText(el.textContent);
          const key = 'top|' + text;
          if (seen.has(key)) return;
          seen.add(key);
          results.push({ selector: 'top-area-leaf', ...describe(el, 40) });
        });
      }
      return results.slice(0, 60);
    },

    current_account_guess() {
      const cands = commands.account_candidates();
      const stopWords = ['消息', '设置', '退出', '首页', '线索', '客服', '工作台', '登录', '帮助', '更多', '抖音来客', '返回', '下载'];
      const byText = {};
      cands.forEach(c => { byText[c.text] = (byText[c.text] || 0) + 1; });
      const ranked = Object.entries(byText)
        .filter(([t]) => t.length >= 2 && t.length <= 40 && !stopWords.some(w => t.includes(w)))
        .sort((a, b) => b[1] - a[1]);
      return ranked.slice(0, 5).map(([text, count]) => ({ text, count }));
    },

    tabs() {
      const tabSelectors = [
        '.byted-tab-item', '.byted-tab', '.byted-tabs-tab',
        '[class*="tab-item"]', '[class*="tab"]', '[class*="Tab"]',
        '[role="tab"]'
      ];
      const out = [];
      const seen = new Set();
      for (const sel of tabSelectors) {
        let els;
        try { els = document.querySelectorAll(sel); } catch (e) { continue; }
        for (const el of Array.from(els).slice(0, 15)) {
          const text = cleanText(el.textContent);
          if (!text || text.length > 30) continue;
          const key = sel + '|' + text;
          if (seen.has(key)) continue;
          seen.add(key);
          const isActive = (typeof el.className === 'string' && el.className.includes('active')) ||
            el.getAttribute('aria-selected') === 'true';
          out.push({ selector: sel, text, active: isActive, ...describe(el, 40) });
        }
      }
      return out.slice(0, 40);
    },

    message_items() {
      const selectors = [
        '[class*="message"]', '[class*="chat"]', '[class*="Chat"]', '[class*="Message"]',
        '[class*="session"]', '[class*="Session"]', '[class*="conversation"]', '[class*="Conversation"]',
        '[class*="list-item"]', '[class*="ListItem"]'
      ];
      const found = [];
      const seen = new Set();
      for (const sel of selectors) {
        let els;
        try { els = document.querySelectorAll(sel); } catch (e) { continue; }
        for (const el of Array.from(els).slice(0, 30)) {
          const text = cleanText(el.textContent);
          if (!text) continue;
          const r = el.getBoundingClientRect();
          if (r.width < 60 || r.height < 20) continue; // 过滤超大容器/小装饰
          const key = sel + '|' + Math.round(r.width) + 'x' + Math.round(r.height) + '|' + text.substring(0, 20);
          if (seen.has(key)) continue;
          seen.add(key);
          found.push({ selector: sel, ...describe(el, 100) });
        }
      }
      return found.slice(0, 50);
    },

    markers() {
      const texts = ['已留资', '广告源', '经营源', '留资', '私信', '线索'];
      const out = [];
      const seen = new Set();
      for (const t of texts) {
        const nodes = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        let count = 0;
        while (nodes.nextNode() && count < 50) {
          const node = nodes.currentNode;
          if (!node.textContent || !node.textContent.includes(t)) continue;
          count++;
          const el = node.parentElement;
          if (!el) continue;
          const key = t + '|' + elPath(el, 3);
          if (seen.has(key)) continue;
          seen.add(key);
          out.push({ marker: t, ...describe(el, 120) });
        }
      }
      return out.slice(0, 60);
    },

    snapshot() {
      return {
        page: commands.page_info(),
        currentAccount: commands.current_account_guess(),
        accountCandidates: commands.account_candidates(),
        tabs: commands.tabs(),
        messageItems: commands.message_items(),
        markers: commands.markers(),
        bodyTextSample: document.body ? cleanText(document.body.innerText).substring(0, 3000) : ''
      };
    },

    test_selectors(params) {
      const selectors = (params.selectors || []).map(s => String(s));
      const out = [];
      for (const sel of selectors) {
        try {
          const els = document.querySelectorAll(sel);
          out.push({
            selector: sel,
            count: els.length,
            first: els.length > 0 ? describe(els[0], 150) : null
          });
        } catch (e) {
          out.push({ selector: sel, error: String(e && e.message || e) });
        }
      }
      return { tested: out.length, results: out };
    },

    find_text(params) {
      const text = String(params.text || '');
      const max = parseInt(params.max) || 10;
      if (!text) return { error: '缺少 text' };
      const out = [];
      const nodes = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      while (nodes.nextNode()) {
        const node = nodes.currentNode;
        const t = node.textContent || '';
        if (!t.includes(text)) continue;
        const el = node.parentElement;
        if (!el) continue;
        let container = el;
        for (let i = 0; i < 4; i++) {
          const p = container.parentElement;
          if (!p) break;
          const pt = p.textContent || '';
          if (pt.includes(text) && p.querySelectorAll('*').length < 40) container = p;
          else break;
        }
        out.push({
          marker: text,
          path: elPath(container, 5),
          textSample: cleanText(container.textContent).substring(0, 200),
          ...describe(container, 200)
        });
        if (out.length >= max) break;
      }
      return { found: out.length, items: out };
    },

    eval_code(params) {
      const code = String(params.code || '');
      if (!code) return { error: '缺少 code' };
      let result;
      try {
        result = new Function(`"use strict"; return (${code});`)();
      } catch (e) {
        try { result = new Function(`"use strict"; ${code}`)(); }
        catch (e2) { return { error: String(e2 && e2.message || e2) }; }
      }
      return { type: typeof result, value: safeJson(result), repr: String(result).substring(0, 500) };
    },

    walk(params) {
      const max = parseInt(params.max) || 80;
      const tagStats = {};
      const classStats = {};
      const all = document.querySelectorAll('*');
      const total = all.length;
      for (const el of Array.from(all).slice(0, 30000)) {
        const tag = el.tagName.toLowerCase();
        tagStats[tag] = (tagStats[tag] || 0) + 1;
        const cls = (typeof el.className === 'string' ? el.className.trim().split(/\s+/) : []);
        for (const c of cls.slice(0, 3)) {
          if (!classStats[c]) classStats[c] = { tag, count: 0 };
          classStats[c].count++;
        }
      }
      const topClasses = Object.entries(classStats)
        .sort((a, b) => b[1].count - a[1].count).slice(0, max)
        .map(([cls, info]) => ({ class: cls, tag: info.tag, count: info.count }));
      return { total, tagStats, topClasses };
    },

    html(params) {
      const selector = String(params.selector || 'body');
      const limit = parseInt(params.limit) || 1;
      const maxLen = parseInt(params.maxLen) || 4000;
      const out = [];
      try {
        const els = document.querySelectorAll(selector);
        for (const el of Array.from(els).slice(0, limit)) {
          let html = el.outerHTML;
          const truncated = html.length > maxLen;
          out.push({
            selector,
            truncated,
            length: html.length,
            html: html.substring(0, maxLen),
            path: elPath(el, 5)
          });
        }
        return { count: els.length, returned: out.length, items: out };
      } catch (e) {
        return { error: String(e && e.message || e) };
      }
    },

    capture(params) {
      const maxLen = parseInt(params.maxLen) || 20000;
      let html = document.documentElement ? document.documentElement.outerHTML : '';
      const truncated = html.length > maxLen;
      return { truncated, length: html.length, html: html.substring(0, maxLen) };
    },

    network(params) {
      const hostFilter = String(params.host || 'life.douyin.com');
      const resources = performance.getEntriesByType('resource')
        .filter(e => e.name.includes(hostFilter))
        .slice(-30)
        .map(e => ({ name: e.name.substring(0, 200), initiatorType: e.initiatorType, duration: Math.round(e.duration) }));
      const nav = performance.getEntriesByType('navigation')[0];
      return {
        resources,
        xhrRecent: (window.__debugXHR || []).slice(-20),
        pageLoad: nav ? {
          domContentLoaded: Math.round(nav.domContentLoadedEventEnd),
          load: Math.round(nav.loadEventEnd)
        } : null
      };
    },

    // 预编译正则搜索文本节点(CSP 禁 eval 场景下替代 eval_code)
    search_text(params) {
      let re;
      try { re = new RegExp(String(params.pattern || ''), String(params.flags || 'i')); }
      catch (e) { return { error: 'bad_regex: ' + String(e && e.message || e) }; }
      const max = parseInt(params.max) || 30;
      const onlyVisible = params.onlyVisible !== false;
      const out = [];
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      while (walker.nextNode() && out.length < max) {
        const node = walker.currentNode;
        const t = cleanText(node.textContent);
        if (!t) continue;
        re.lastIndex = 0;
        const m = re.exec(t);
        if (!m) continue;
        const el = node.parentElement;
        if (!el) continue;
        if (onlyVisible && el.offsetParent === null) continue;
        out.push({
          match: m[0].slice(0, 60),
          text: t.slice(0, 160),
          tag: el.tagName.toLowerCase(),
          classes: (typeof el.className === 'string' ? el.className.trim() : '').slice(0, 100),
          path: elPath(el, 6)
        });
      }
      return { count: out.length, items: out };
    },

    // 会话列表完整结构转储(重写 content.js SELECTORS 用)
    conversation_items(params) {
      const max = parseInt(params.max) || 25;
      const items = Array.from(document.querySelectorAll('[class*="conversationItem"]')).slice(0, max);
      return {
        count: items.length,
        items: items.map((it, idx) => {
          const leafs = Array.from(it.querySelectorAll('div,span,p'))
            .filter(el => el.children.length === 0 && cleanText(el.textContent))
            .map(el => ({
              cls: (typeof el.className === 'string' ? el.className.trim() : '').slice(0, 90),
              text: cleanText(el.textContent).slice(0, 100)
            }));
          const imgs = Array.from(it.querySelectorAll('img')).map(img => ({
            cls: (typeof img.className === 'string' ? img.className.trim() : '').slice(0, 60),
            srcHead: (img.src || '').slice(0, 60),
            w: img.naturalWidth || img.width || 0
          }));
          const sups = Array.from(it.querySelectorAll('sup')).map(s => ({
            cls: (typeof s.className === 'string' ? s.className.trim() : '').slice(0, 80),
            text: cleanText(s.textContent).slice(0, 30),
            transform: (() => { const b = s.querySelector('[class*="animated-number-bit"]'); return b ? (b.style.transform || '') : ''; })()
          }));
          return {
            idx,
            active: it.className.includes('Active') || !!it.querySelector('[class*="Active"]'),
            classes: it.className.trim().slice(0, 120),
            text: cleanText(it.textContent).slice(0, 200),
            leafs: leafs.slice(0, 14), imgs, sups
          };
        })
      };
    },

    // 消息窗口结构转储(留资卡片/系统消息/时间戳的真实 class)
    window_messages(params) {
      const max = parseInt(params.max) || 40;
      const items = Array.from(document.querySelectorAll('.csUI-MessageItem, [class*="csUI-MsgTimeGap"], [class*="MsgTimeGap"]')).slice(0, max);
      const title = document.querySelector('[class*="msgTitle"]');
      return {
        windowTitle: title ? cleanText(title.textContent).slice(0, 80) : '',
        count: items.length,
        items: items.map(el => {
          const cls = (typeof el.className === 'string' ? el.className.trim() : '');
          if (cls.includes('MsgTimeGap')) return { kind: 'timegap', text: cleanText(el.textContent).slice(0, 40) };
          const dir = el.querySelector('.csUI-NormalMessage_left') ? 'left'
            : (el.querySelector('.csUI-NormalMessage_right') ? 'right' : 'other');
          const leafs = Array.from(el.querySelectorAll('div,span,p,img,button'))
            .filter(x => x.children.length === 0 || x.tagName === 'IMG')
            .map(x => ({
              tag: x.tagName.toLowerCase(),
              cls: (typeof x.className === 'string' ? x.className.trim() : '').slice(0, 80),
              text: cleanText(x.textContent).slice(0, 80),
              src: x.tagName === 'IMG' ? (x.src || '').slice(0, 70) : undefined,
              alt: x.getAttribute && x.getAttribute('alt') || undefined
            }));
          return { kind: 'msg', dir, classes: cls.slice(0, 100), text: cleanText(el.textContent).slice(0, 160), leafs: leafs.slice(0, 16) };
        })
      };
    },

    // content.js 健康自检: 读 <html> 上 content 写的 data-ll-* 探针
    // (判断生产页到底跑的是哪版 content、循环是否活着、卡在哪一步)
    content_health() {
      var d = document.documentElement.dataset || {};
      var out = {
        version: d.llVersion || '(content.js 未加载或旧版无探针)',
        bootTs: d.llBootTs || '',
        scanTs: d.llScanTs || '',
        scanAgeSec: d.llScanTs ? Math.round((Date.now() - +d.llScanTs) / 1000) : null,
        convCount: d.llConvCount,
        winMsgs: d.llWinMsgs,
        custMsgs: d.llCustMsgs,
        saw: d.llSaw || '',
        skipNote: d.llSkipNote || '',
        turbo: d.llTurbo || 'off',
        error: d.llError || '',
        storageOk: d.llStorageOk || '',
        now: Date.now()
      };
      if (d.llBootTs) out.bootAgeSec = Math.round((Date.now() - +d.llBootTs) / 1000);
      return out;
    },

    mutation_test(params) {
      const selector = String(params.selector || '');
      const duration = parseInt(params.duration) || 3000;
      if (!selector) return { error: '缺少 selector' };
      return new Promise(resolve => {
        const hits = [];
        const obs = new MutationObserver(muts => {
          for (const m of muts) {
            for (const node of m.addedNodes) {
              if (node.nodeType !== 1) continue;
              if (node.matches && node.matches(selector)) {
                hits.push({ path: elPath(node, 4), text: cleanText(node.textContent).substring(0, 80) });
              }
              if (node.querySelectorAll) {
                node.querySelectorAll(selector).forEach(n => hits.push({
                  path: elPath(n, 4), text: cleanText(n.textContent).substring(0, 80)
                }));
              }
            }
          }
        });
        obs.observe(document.body, { childList: true, subtree: true });
        setTimeout(() => {
          obs.disconnect();
          resolve({ observed_ms: duration, count: hits.length, hits: hits.slice(0, 30) });
        }, duration);
      });
    }
  };

  // ------------------------------------------------------------ dispatch
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (!msg || msg.type !== 'DEBUG_COMMAND') return false;
    const cmd = msg.cmd;
    const params = msg.params || {};
    const startedAt = Date.now();
    try {
      const handler = commands[cmd];
      if (!handler) {
        sendResponse({ ok: false, cmd, error: `unknown_command: ${cmd}`, ms: Date.now() - startedAt });
        return false;
      }
      const r = handler(params);
      if (r && typeof r.then === 'function') {
        r.then(res => sendResponse({ ok: true, cmd, result: res, ms: Date.now() - startedAt }))
          .catch(err => sendResponse({ ok: false, cmd, error: String(err && err.message || err), ms: Date.now() - startedAt }));
        return true; // 异步响应
      }
      sendResponse({ ok: true, cmd, result: r, ms: Date.now() - startedAt });
    } catch (e) {
      sendResponse({ ok: false, cmd, error: String(e && e.message || e), ms: Date.now() - startedAt });
    }
    return false;
  });

  // ------------------------------------------------------------ network hook
  if (!window.__debugXHR) {
    window.__debugXHR = [];
    const origOpen = XMLHttpRequest.prototype.open;
    const origSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (m, u) {
      this.__m = m; this.__u = String(u || '');
      return origOpen.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function (body) {
      try {
        window.__debugXHR.push({ t: Date.now(), m: this.__m, u: (this.__u || '').substring(0, 300) });
        if (window.__debugXHR.length > 200) window.__debugXHR.shift();
      } catch (e) { /* noop */ }
      return origSend.apply(this, arguments);
    };
  }

  // ------------------------------------------------------------ 启动自轮询
  dbgStart();
})();
