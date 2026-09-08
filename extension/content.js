// LeadsLinker 内容脚本 - 检测抖音来客会话页的留资线索并转发
//
// 检测策略 (2026-09-02 基于生产真实 DOM 重写, v1.4 全量容错):
//   1. 全量扫描: 所有"最近(今天/12h内或有未读)"的会话都会点开扫一遍, 与平台标签无关。
//      平台"已留资"标签会漏: 客户直接发微信号(如 88600772)或发微信二维码图片时,
//      来客只标"广告源"。标签仅用于排队优先级。
// v3.0.1: 姓名净化(sanitizeName, 标签词≠昵称)+虚拟列表滚动刷新+未就绪不误报(ADR-0007)
//   2. 会话窗口提取: 只取客户(left)消息, 联系方式三级容错:
//      手机号(允许空格/横线) > "微信:xxx"标注 > 整条即微信号ID > 二维码图片
//      时间取该消息上方最近的 .csUI-MsgTimeGap (完整日期, 最可靠)
//   3. 稳定 convId 去重: 窗口标题 msgTitle 含 "conv\d+", 持久化到 storage,
//      页面刷新/重启后不重复转发; 再叠加手机号/微信维度 + OneBot CSV 查重
//   4. 自动点开分级: 「已留资」/有未读 → 2.5s 防抖后即自动点开检查(不漏客户优先);
//      普通复查 → 20s 空闲才点(不打扰正在打字的客服); 页面在后台则随时可点
//
// 页面结构锚点(稳定, 无构建哈希): csUI-* 组件类 / conversationItem* / msgTitle* /
//   leadsTag* adTag* topTag* / rc-virtual-list / byted-badge

(function () {
  'use strict';

  var LOG = '[LeadsLinker]';

  var LL_VERSION = '1.7.0';
  // 跨 isolated-world 健康自检: content 与 debug-content 同属扩展的同一 isolated world,
  // 但 DOM 属性是最稳的共享面。debug-content 用 content_health 命令读这些 dataset。
  function setHealth(k, v) {
    try { document.documentElement.dataset[k] = String(v); } catch (e) {}
  }

  var SEL = {
    convItem: '[class*="conversationItem"]',
    convActive: '[class*="conversationActiveItem"]',
    convName: '[class*="conversationName"]',
    msgItem: '.csUI-MessageItem',
    msgTimeGap: '.csUI-MsgTimeGap',
    msgLeft: '.csUI-NormalMessage_left',
    msgRight: '.csUI-NormalMessage_right',
    msgText: '.csUI-Text',
    msgLinkText: '.csUI-TextLink_text',
    msgSender: '.csUI-MessageNickname_name',
    msgScroll: '[class*="overflow-y-scroll"]',
    msgTitle: '[class*="msgTitle"]',
    more: '.csUI-More, [class*="csUI-More"]',
    abstract: '[class*="csUI-MessageAbstract"]',
    avatar: '.csUI-Avatar_img, [class*="avatar"] img, img[class*="avatar"]'
  };

  var PHONE_RE = /1[3-9]\d{9}/;
  var WECHAT_LABEL_RE = /(?:微信|wx|v信|vx|威信)[：:\s]*([a-zA-Z][a-zA-Z0-9_\-]{4,19}|[0-9]{5,20})/i;
  // 独立微信号消息: 整条就是一个 ID(字母开头或5位以上数字), 如 "88600772" / "abcd_123"
  // 抖音来客不会把这种识别为"已留资", 本系统容错提取
  var STANDALONE_WECHAT_RE = /^\s*([a-zA-Z][a-zA-Z0-9_\-]{4,19}|[0-9]{5,20})\s*$/;
  var CONVID_RE = /conv(\d{6,})/i;
  var LEAD_TAGS = ['已留资', '广告源', '经营源'];

  // 姓名净化(2026-09-08): 标签词("广告源/已留资/经营源")不是昵称。
  // 虚拟列表未渲染昵称时, 窗口标题会是 "广告源已留资convXXX" 这种纯标签串,
  // 旧 titleName 正则会把 "广告源" 吃成客户名(洋崽仔被识别成广告源的根因)。
  // 规则: 空/正在输入/等于标签词/仅由标签词组成 → 一律视为「昵称未就绪」。
  function sanitizeName(n) {
    var t = (n || '').trim();
    if (!t || isTypingText(t)) return '';
    var rest = t;
    for (var i = 0; i < LEAD_TAGS.length; i++) {
      rest = rest.split(LEAD_TAGS[i]).join('');
    }
    rest = rest.trim();
    if (!rest) return '';            // 整串都是标签词 → 没有真名
    if (t !== rest && rest.length < 2) return '';  // 剥掉标签后只剩 1 字符 → 可疑
    return t;
  }
  var OWNER_HINTS = ['官方号', '安信德', '创客龙', '客服', '机器人', '用户触达', '智能'];

  var CONFIG = {
    detectionInterval: 3000,
    onlyTodayMessages: true,
    deepScanIntervalMs: 5000,    // 逐个点开的间隔
    idleDelayMs: 20000,          // 普通复查: 空闲多久才点开(不打扰客服)
    urgentIdleMs: 2500           // 已留资/有未读: 近乎即时点开(漏客户更严重)
  };

  var currentAccount = null;
  var lastUserActivity = Date.now();
  var busy = { deepQueue: false };          // 深度扫描互斥
  var clickedThisSession = new Set();       // 本页面会话已主动点开过的 name(防抖)
  var forwardedNames = new Set();        // 已成功转发的昵称(防补扫反复点开同一线索)
  var forwardingNow = new Set();         // 正在查重/转发的 lead.key

  // ------------------------------------------------------------ 工具
  function log() { try { console.log.apply(console, [LOG].concat([].slice.call(arguments))); } catch (e) {} }
  function warn() { try { console.warn.apply(console, [LOG].concat([].slice.call(arguments))); } catch (e) {} }
  function text(el) { return el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : ''; }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  function getTodayDate() {
    var now = new Date();
    return now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0') + '-' + String(now.getDate()).padStart(2, '0');
  }

  function parseTime(timeStr) {
    if (!timeStr) return null;
    var s = String(timeStr).trim(), m, now = new Date();
    if ((m = s.match(/(\d{4})-(\d{1,2})-(\d{1,2})[ T\u3000]+(\d{1,2}):(\d{2})(?::(\d{2}))?/))) {
      return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], m[6] ? +m[6] : 0);
    }
    if ((m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/))) return new Date(+m[1], +m[2] - 1, +m[3]);
    if ((m = s.match(/^(?:今天|今日)\s*(\d{1,2}):(\d{2})/))) return new Date(now.getFullYear(), now.getMonth(), now.getDate(), +m[1], +m[2]);
    if ((m = s.match(/^(?:昨天|昨日)\s*(\d{1,2}):(\d{2})/))) { var d1 = new Date(now); d1.setDate(d1.getDate() - 1); d1.setHours(+m[1], +m[2], 0, 0); return d1; }
    if ((m = s.match(/^(?:前天)\s*(\d{1,2}):(\d{2})/))) { var d2 = new Date(now); d2.setDate(d2.getDate() - 2); d2.setHours(+m[1], +m[2], 0, 0); return d2; }
    if ((m = s.match(/^(?:今天|昨日|昨天|前天|今日)$/))) {
      var dd = new Date(now); if (/昨/.test(s)) dd.setDate(dd.getDate() - 1); if (/前天/.test(s)) dd.setDate(dd.getDate() - 2);
      dd.setHours(0, 0, 0, 0); return dd;
    }
    if ((m = s.match(/(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})/))) return new Date(now.getFullYear(), +m[1] - 1, +m[2], +m[3], +m[4]);
    if ((m = s.match(/^(\d{1,2})-(\d{1,2})$/))) return new Date(now.getFullYear(), +m[1] - 1, +m[2]);
    if ((m = s.match(/^(\d{1,2}):(\d{2})$/))) return new Date(now.getFullYear(), now.getMonth(), now.getDate(), +m[1], +m[2]);
    if (/^刚刚$/.test(s)) return now;
    if ((m = s.match(/^(\d+)\s*分钟前$/))) return new Date(Date.now() - +m[1] * 60000);
    if ((m = s.match(/^(\d+)\s*小时前$/))) return new Date(Date.now() - +m[1] * 3600000);
    if ((m = s.match(/^(\d+)\s*天前$/))) return new Date(Date.now() - +m[1] * 86400000);
    return null;
  }

  function isWithinHours(timeStr, hours) {
    var t = parseTime(timeStr);
    if (!t) return false;
    var diff = Date.now() - t.getTime();
    return diff >= -60000 && diff <= hours * 3600 * 1000;
  }

  function isToday(timeStr) {
    if (!CONFIG.onlyTodayMessages) return true;
    var t = parseTime(timeStr);
    if (!t) return false;
    return t.getFullYear() + '-' + String(t.getMonth() + 1).padStart(2, '0') + '-' + String(t.getDate()).padStart(2, '0') === getTodayDate();
  }

  // ------------------------------------------------------------ 持久化去重
  // forwardedLeads: { "c:convId|p:phone|w:wechat": {date, ts} } 按天保留
  var forwardedCache = null;
  function loadForwarded() {
    return new Promise(function (resolve) {
      if (forwardedCache) return resolve(forwardedCache);
      try {
        chrome.storage.local.get('forwardedLeads', function (r) {
          forwardedCache = r.forwardedLeads || {};
          resolve(forwardedCache);
        });
      } catch (e) { forwardedCache = {}; resolve(forwardedCache); }
    });
  }
  function rememberForwarded(keys) {
    loadForwarded().then(function (cache) {
      var today = getTodayDate();
      keys.forEach(function (k) { cache[k] = { date: today, ts: Date.now() }; });
      // 清理 3 天前的记录
      Object.keys(cache).forEach(function (k) {
        var age = Date.now() - (cache[k].ts || 0);
        if (age > 3 * 86400000) delete cache[k];
      });
      try { chrome.storage.local.set({ forwardedLeads: cache }); } catch (e) {}
    });
  }
  function alreadyForwarded(keys) {
    return loadForwarded().then(function (cache) {
      var today = getTodayDate();
      return keys.some(function (k) { return cache[k] && cache[k].date === today; });
    });
  }

  // ------------------------------------------------------------ 账号检测
  function detectCurrentAccount() {
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      var t = text(walker.currentNode);
      var m = t.match(/^账号(.{1,40})$/);
      if (!m) continue;
      var el = walker.currentNode.parentElement;
      if (el && el.offsetParent !== null) {
        var account = m[1].trim();
        if (account && account !== currentAccount) {
          currentAccount = account;
          log('检测到当前账号:', account);
          try { chrome.runtime.sendMessage({ type: 'ACCOUNT_CHANGED', account: account }, function () {}); } catch (e) {}
        }
        return;
      }
    }
    var cand = document.querySelector('[class*="dyFilter"] [class*="w-fit"], [class*="userInfo"] [class*="overflow-hidden"]');
    var ct = text(cand).replace(/^账号/, '').trim();
    if (ct && ct.length > 1 && ct.length < 50 && ct !== currentAccount &&
        ct.indexOf('消息') < 0 && ct.indexOf('设置') < 0 && ct.indexOf('退出') < 0) {
      currentAccount = ct;
      log('检测到当前账号(via selector):', currentAccount);
      try { chrome.runtime.sendMessage({ type: 'ACCOUNT_CHANGED', account: currentAccount }, function () {}); } catch (e) {}
    }
  }

  // ------------------------------------------------------------ 会话项解析
  function convItems() { return Array.prototype.slice.call(document.querySelectorAll(SEL.convItem)); }

  // 虚拟列表懒加载刷新(2026-09-08, 用户实测经验):
  // rc-virtual-list 只渲染可视区条目, 未滚动到的会话根本没有头像/昵称 DOM。
  // 「滚动到底再滚回顶」会强制 React 把全部条目依次挂载一遍, 挂载期间
  // MutationObserver 会持续触发重扫。返回 Promise, 完成后列表为"已尽力加载"态。
  var lastListRefresh = 0;
  function listScrollEl() {
    return document.querySelector('div.rc-virtual-list-holder')
      || document.querySelector('[class*="rc-virtual-list"] [class*="holder"]');
  }
  function refreshListScroll() {
    var el = listScrollEl();
    if (!el) return Promise.resolve(false);
    setHealth('llListRefresh', Date.now());
    var top = el.scrollTop, max = el.scrollHeight - el.clientHeight;
    var stepDown = function () {
      if (el.scrollTop >= max - 4) return Promise.resolve();
      el.scrollTop = Math.min(el.scrollHeight, el.scrollTop + el.clientHeight * 0.9);
      return sleep(160).then(stepDown);
    };
    return stepDown().then(function () { return sleep(500); })
      .then(function () { el.scrollTop = top; return sleep(400); })
      .then(function () { return true; })
      .catch(function () { return false; });
  }

  // 有线索标记/未读但昵称未就绪的条目数 → 决定是否需要滚动刷新
  function countUnnamedLeadItems() {
    var n = 0;
    var items = convItems();
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      if ((itemHasLeadMark(it) || unreadCount(it) > 0) && !itemName(it)) n++;
    }
    return n;
  }

  function itemHasLeadMark(item) {
    var t = text(item);
    return t.indexOf('已留资') >= 0;
  }

  function itemLeadType(item) {
    var t = text(item);
    if (t.indexOf('已留资') >= 0) return '已留资';
    if (t.indexOf('广告源') >= 0) return '广告源';
    if (t.indexOf('经营源') >= 0) return '经营源';
    return '未知';
  }

  function itemName(item) {
    var n = sanitizeName(text(item.querySelector(SEL.convName)));
    if (n && !isTypingText(n)) return n;
    return '';
  }

  // 防抖键: 昵称拿不到(对方正在输入等) → 用条目前 50 字符做键, 避免空键失效反复点
  function itemKey(item) {
    return itemName(item) || text(item).slice(0, 50);
  }

  function isTypingText(t) {
    return !t || t.indexOf('正在输入') >= 0 || /typing/i.test(t);
  }

  // 列表时间: 遍历叶子节点找"纯时间样式"文本(角标动画数字是单字符叶子, 不会误中)
  function itemTime(item) {
    var leafs = item.querySelectorAll('div,span,p');
    for (var i = 0; i < leafs.length; i++) {
      var el = leafs[i];
      if (el.children.length > 0) continue;
      var t = text(el);
      if (!t || t.length > 16) continue;
      if (/^\d{1,2}:\d{2}$/.test(t) || /^(今天|昨天|前天)\s*\d{1,2}:\d{2}$/.test(t) ||
          /^\d{1,2}-\d{1,2}\s*\d{1,2}:\d{2}$/.test(t) || /^\d{4}-\d{1,2}-\d{1,2}$/.test(t)) {
        return t;
      }
    }
    return '';
  }

  // 未读数: byted-badge 动画数字 transform -N00% → 第 N 帧
  function unreadCount(item) {
    try {
      var bit = item.querySelector('sup[class*="byted-badge-sup"] [class*="animated-number-bit"]');
      if (!bit) return 0;
      var m = (bit.style.transform || '').match(/translate3d\(\s*0px[,\s]+(-?\d+(?:\.\d+)?)%/)
           || (bit.style.transform || '').match(/translate\(\s*0(?:px)?[,\s]+(-?\d+(?:\.\d+)?)%/);
      if (!m) return 0;
      var frames = Array.prototype.slice.call(bit.children)
        .map(function (p) { return text(p); })
        .filter(function (s) { return s !== ''; });
      if (!frames.length) return 0;
      var offset = Math.round(Math.abs(parseFloat(m[1])) / 100) % frames.length;
      var val = parseInt(frames[offset], 10);
      return isNaN(val) ? 0 : val;
    } catch (e) { return 0; }
  }

  // ------------------------------------------------------------ 消息窗口解析
  function windowTitle() {
    var els = document.querySelectorAll(SEL.msgTitle);
    for (var i = 0; i < els.length; i++) {
      var t = text(els[i]);
      if (t && t.length > 2) return t;
    }
    return '';
  }

  function titleConvId(title) {
    var m = (title || '').match(CONVID_RE);
    return m ? m[1] : '';
  }

  function titleName(title) {
    // "桃O总广告源已留资conv..." → 昵称 = 开头到第一个标签词/conv 之前
    // (2026-09-08) 标题以标签词开头(虚拟列表未渲染昵称, 如 "广告源已留资convXXX")
    // 时不存在昵称前缀 —— 旧正则会把 "广告源" 吃成名字(洋崽仔误判根因), 直接判未就绪。
    var t = (title || '').trim();
    for (var i = 0; i < LEAD_TAGS.length; i++) {
      if (t.indexOf(LEAD_TAGS[i]) === 0) return '';
    }
    var m = t.match(/^(.{1,24}?)(?:广告源|已留资|经营源|置顶|私信|conv\d)/i);
    return m ? sanitizeName(m[1].trim()) : sanitizeName(t.length <= 24 ? t : '');
  }

  // 按 DOM 顺序解析当前会话窗口: 每条消息带最近 timegap
  function parseMessages() {
    var first = document.querySelector(SEL.msgItem);
    if (!first) return null;
    var scope = document;
    var scroller = document.querySelector(SEL.msgScroll);
    if (scroller && scroller.querySelector(SEL.msgItem)) scope = scroller;
    var markers = scope.querySelectorAll(SEL.msgTimeGap + ', ' + SEL.msgItem);
    var curTime = '';
    var msgs = [];
    markers.forEach(function (el) {
      if (el.matches && el.matches(SEL.msgTimeGap)) { curTime = text(el); return; }
      var left = el.querySelector(SEL.msgLeft);
      var right = el.querySelector(SEL.msgRight);
      if (!left && !right) return;
      var box = left && !right ? left : right;
      var dir = left && !right ? 'left' : 'right';
      var parts = [];
      box.querySelectorAll(SEL.msgText + ', ' + SEL.msgLinkText).forEach(function (x) {
        var t = text(x);
        if (t) parts.push(t);
      });
      // 客户发的图片(微信二维码/截图)也记录 —— 平台对图片留资不给"已留资"标签, 需容错
      var pics = [];
      box.querySelectorAll('img').forEach(function (im) {
        var w = im.naturalWidth || im.width || 0;
        var h = im.naturalHeight || im.height || 0;
        if (!im.src || im.src.indexOf('data:image/svg') === 0) return;
        var cls = (typeof im.className === 'string' ? im.className : '') + ' ' + (im.parentElement && im.parentElement.className || '');
        if (/avatar/i.test(cls)) return; // 排除头像(头像是大图 naturalWidth 也过阈值)
        if (w >= 80 && h >= 80 && Math.abs(w - h) <= w * 0.6) pics.push(im.src);
      });
      msgs.push({
        dir: dir,
        time: curTime,
        sender: text(el.querySelector(SEL.msgSender)),
        text: parts.join(' '),
        pics: pics
      });
    });
    return msgs;
  }

  function extractContact(str) {
    var phone = (str.replace(/[\s\-－]/g, '').match(PHONE_RE) || [])[0] || '';
    var wechat = '';
    var wm = str.match(WECHAT_LABEL_RE);
    if (wm) wechat = wm[1];
    if (!phone && !wechat && str.length <= 30) {
      var sm = str.match(STANDALONE_WECHAT_RE);
      if (sm) wechat = sm[1]; // 整条消息就是个微信号/ID(如 "88600772")
    }
    return { phone: phone, wechat: wechat };
  }

  function extractQrcode(item) {
    var imgs = (item || document).querySelectorAll(SEL.msgLeft + ' img');
    for (var i = imgs.length - 1, n = 0; i >= 0 && n < 8; i--, n++) {
      var src = imgs[i].src || '';
      if (src.indexOf('data:image/') === 0) {
        var w = imgs[i].naturalWidth || imgs[i].width || 0;
        if (w >= 60 && w <= 600) return src;
      }
      if (/qr|qrcode|scan/i.test(src)) return src;
    }
    return '';
  }

  function extractAvatar(item) {
    try {
      var img = (item || document).querySelector(SEL.avatar);
      if (img && img.src) return img.src;
    } catch (e) {}
    return '';
  }

  // ------------------------------------------------------------ 核心: 从当前打开的窗口提取线索
  // opts: { expectName, hoursFilter, source }
  function extractLeadFromOpenedWindow(opts) {
    var msgs = parseMessages();
    if (!msgs) return Promise.resolve(null);
    var title = windowTitle();
    var convId = titleConvId(title);
    var tName = titleName(title);

    // 归属校验: 期望昵称与窗口标题不符 → SPA 切换错位, 放弃。
    // 例外：标题是"对方正在输入…"这类正在输入提示时，不当作昵称，跳过该校验，
    // 否则聊天中对方打字会让 青出于蓝 vs 对方正在输入 误判为不匹配而漏检。
    if (opts.expectName && tName && !isTypingText(tName)
        && tName !== opts.expectName && title.indexOf(opts.expectName) < 0) {
      log('窗口与会话不匹配, 跳过:', opts.expectName, 'vs', tName);
      return Promise.resolve(null);
    }

    var customerMsgs = msgs.filter(function (m) {
      return m.dir === 'left' && m.text &&
        !OWNER_HINTS.some(function (h) { return (m.sender || '').indexOf(h) >= 0; });
    });
    setHealth('llWinMsgs', msgs.length);
    setHealth('llCustMsgs', customerMsgs.length);
    if (!customerMsgs.length) { setHealth('llSkipNote', 'no customer(left) msgs'); return Promise.resolve(null); }

    // 从最新往前找第一条含联系方式的客户消息。优先级: 手机号 > 微信号 > 二维码图片
    // (留资常是"点击发送手机号"产生的独立消息; 图片二维码平台不给"已留资"标签, 容错兜底)
    var leadMsg = null, contact = null, picLead = null;
    for (var i = customerMsgs.length - 1; i >= 0; i--) {
      var m = customerMsgs[i];
      var c = extractContact(m.text || '');
      if (c.phone || c.wechat) { leadMsg = m; contact = c; break; }
      if (!picLead && m.pics && m.pics.length) picLead = m;
    }
    if (!leadMsg && picLead) {
      // 图片二维码留资: 无文字联系方式, 用 convId/昵称 去重
      var picTime = picLead.time || '';
      var picOk = opts.hoursFilter ? isWithinHours(picTime, opts.hoursFilter) : isToday(picTime);
      if (!picTime || !picOk) return Promise.resolve(null);
      var picReal = (opts.expectName && !isTypingText(opts.expectName)) ? opts.expectName
        : (tName && !isTypingText(tName) ? tName : '');
      var picName = picReal || '未知客户';
      return Promise.resolve({
        key: { conv: convId, phone: '', wechat: '' },
        type: itemLeadType(document.querySelector(SEL.convActive) || document.body) || '未知',
        nameIsFallback: !picReal,
        messageTime: picTime,
        name: picName,
        phone: '',
        wechat: '',
        note: '客户发送了图片(疑似微信二维码), 请人工查看会话',
        qrcode: picLead.pics[picLead.pics.length - 1],
        avatar: extractAvatar(null),
        timestamp: new Date().toISOString(),
        pageUrl: window.location.href,
        pageTitle: document.title
      });
    }
    if (!leadMsg) { setHealth('llSkipNote', 'no contact in customer msgs (phone/wechat/pic)'); return Promise.resolve(null); }

    // 时间窗口: 实时=今天; 补扫=N 小时内
    var msgTime = leadMsg.time || '';
    var ok = opts.hoursFilter ? isWithinHours(msgTime, opts.hoursFilter) : isToday(msgTime);
    if (!msgTime || !ok) {
      log('窗口外线索, 跳过:', tName || opts.expectName, msgTime || '(no time)');
      setHealth('llSkipNote', 'time out of window: ' + msgTime);
      return Promise.resolve(null);
    }
    setHealth('llSkipNote', '');

    var realName = (opts.expectName && !isTypingText(opts.expectName)) ? opts.expectName
      : (tName && !isTypingText(tName) ? tName : '');
    var name = realName || (contact.phone ? '客户' + contact.phone.slice(-4) : (contact.wechat || '未知客户'));

    var noteSrc = customerMsgs.filter(function (m) { return m !== leadMsg; }).map(function (m) { return m.text; });
    var note = (leadMsg.text.replace(contact.phone, '').replace(contact.wechat, '')
      + (noteSrc.length ? ' | 其他消息: ' + noteSrc.slice(-3).join(' ; ') : ''))
      .replace(/\s+/g, ' ').trim().substring(0, 300);

    var type = itemLeadType(document.querySelector(SEL.convActive) || document.body) || '未知';
    return Promise.resolve({
      key: { conv: convId, phone: contact.phone, wechat: contact.wechat },
      type: type,
      nameIsFallback: !realName,
      messageTime: msgTime,
      name: name,
      phone: contact.phone,
      wechat: contact.wechat,
      note: note,
      qrcode: extractQrcode(null),
      avatar: extractAvatar(null),
      timestamp: new Date().toISOString(),
      pageUrl: window.location.href,
      pageTitle: document.title
    });
  }

  // 点开一个会话并深度提取
  var deepBusy = false;
  function openAndExtract(item, opts) {
    if (deepBusy) return Promise.resolve(null);
    deepBusy = true;
    try { item.click(); } catch (e) {}
    return sleep(1300).then(function () {
      // 昵称异步渲染容错: 点开后再最多等 ~5s; 列表项昵称与窗口标题昵称
      // 渲染时机不同, 任一就绪即可(sanitizeName 会拦住标签词假名)。
      var tries = 0;
      var waitName = function () {
        if (itemName(item) || tries++ >= 6) return Promise.resolve();
        var t = titleName(windowTitle());
        if (t) return Promise.resolve();
        return sleep(800).then(waitName);
      };
      return waitName();
    }).then(function () {
      // 尝试点"加载更多"取历史(最多2次)
      var clicks = 0;
      var chain = function () {
        var more = document.querySelector(SEL.more);
        if (more && clicks < 2 && more.offsetParent !== null) {
          clicks++;
          try { more.click(); } catch (e) {}
          return sleep(900).then(chain);
        }
        return Promise.resolve();
      };
      return chain();
    }).then(function () {
      return extractLeadFromOpenedWindow({
        expectName: itemName(item),
        hoursFilter: opts.hoursFilter,
        source: opts.source
      });
    }).catch(function (e) {
      warn('深度提取异常:', e && e.message || e);
      return null;
    }).then(function (lead) {
      deepBusy = false;
      return lead;
    });
  }

  // ------------------------------------------------------------ 查重 + 转发管线
  // 昵称容错(2026-09-02): 后台来新客时列表昵称异步渲染慢半拍 → 会拿兜底名(客户5129)。
  // 策略: 名字没就绪 → 存现场到 storage → 择机(页面后台/空闲, 且距上次刷新>3min)自动刷新
  //       一次 → 重扫拿到真名再转发; 刷新过一次仍拿不到 → 接受兜底名, 绝不死循环。
  //       另有开机保险定时器: 若刷新后线索一直没被重新发现, 超时用兜底名转发, 保证不丢。
  function readStorage(k) {
    return new Promise(function (res) {
      try { chrome.storage.local.get(k, function (r) { res(r[k] || null); }); } catch (e) { res(null); }
    });
  }
  function writeStorage(obj) { try { chrome.storage.local.set(obj); } catch (e) {} }
  function removeStorage(k) { try { chrome.storage.local.remove(k); } catch (e) {} }

  function leadKeys(lead) {
    var keys = [];
    if (lead.key.conv) keys.push('c:' + lead.key.conv);
    if (lead.key.phone) keys.push('p:' + lead.key.phone);
    if (lead.key.wechat) keys.push('w:' + lead.key.wechat);
    return keys;
  }

  function maybeSendLead(lead) {
    if (!lead) return;
    var keys = leadKeys(lead);
    if (!keys.length) return;
    var dedupId = keys.join('|');
    setHealth('llSaw', dedupId);
    if (forwardingNow.has(dedupId)) return;

    if (lead.nameIsFallback && lead.key.conv) {
      handleFallbackName(lead, keys);
      return;
    }
    // 真名到达 → 若有同名会话的等待记录, 说明刷新后名字已恢复, 清掉
    readStorage('nameFixPending').then(function (fix) {
      if (fix && fix.conv === lead.key.conv) removeStorage('nameFixPending');
      proceedSend(lead, keys);
    });
  }

  function handleFallbackName(lead, keys) {
    readStorage('nameFixPending').then(function (fix) {
      var fresh = fix && fix.conv === lead.key.conv && (Date.now() - fix.ts) < 30 * 60000;
      if (fresh && fix.attempt >= 2) {
        // 滚动刷新 + 整页刷新都试过 → 不再等, 用兜底名转发(绝不丢线索)
        removeStorage('nameFixPending');
        setHealth('llNameFix', 'gave up, keep fallback');
        proceedSend(lead, keys);
        return;
      }
      if (fresh) return; // 等待刷新/重扫中, 本条暂不转发
      // 新记录: 先做轻量的「虚拟列表滚动刷新」(用户实测: 滚下去再滚回来就加载出
      // 头像/昵称), 无效才升级到整页 reload —— 不再让标签词假名抢先入库。
      writeStorage({ nameFixPending: { conv: lead.key.conv, lead: lead, attempt: 0, ts: Date.now() } });
      setHealth('llNameFix', 'pending scroll-refresh conv=' + lead.key.conv);
      log('昵称未就绪, 先滚动刷新虚拟列表重试:', lead.name || '', lead.phone || lead.wechat);
      lastListRefresh = Date.now();
      refreshListScroll().then(function () {
        readStorage('nameFixPending').then(function (fix2) {
          if (!fix2) return;
          fix2.attempt = 1;
          writeStorage({ nameFixPending: fix2 });
          setTimeout(detectLeads, 500);
          // 滚动刷新没救回来 → 30s 后升级整页 reload(原机制)
          setTimeout(scheduleNameFixReload, 30000);
        });
      });
    });
  }

  function scheduleNameFixReload(tries) {
    readStorage('nameFixPending').then(function (fix) {
      if (!fix || fix.attempt >= 2) return; // 已整页刷新过/已清 → 不再刷
      var quiet = document.hidden || (Date.now() - lastUserActivity) > 10000;
      readStorage('llLastReloadTs').then(function (lastTs) {
        var spaced = !lastTs || (Date.now() - lastTs) > 3 * 60000;
        if (quiet && spaced) {
          fix.attempt = 2;
          writeStorage({ nameFixPending: fix, llLastReloadTs: Date.now() });
          log('滚动刷新无效, 自动刷新页面以获取真实昵称...');
          setTimeout(function () { try { location.reload(); } catch (e) {} }, 400);
        } else if (tries < 6) {
          setTimeout(function () { scheduleNameFixReload(tries + 1); }, 30000);
        }
        // tries 用尽仍不满足条件 → 不动作, 由开机保险定时器在超时后用兜底名转发
      });
    });
  }

  // 开机保险: 页面加载后若存在 nameFixPending(上次昵称未就绪存的现场):
  //   - 30s/90s 时仍在 → 说明刷新后也没能重新发现该线索 → 用存下的兜底名转发, 不丢客户
  function recoverNameFix() {
    [30000, 90000].forEach(function (ms) {
      setTimeout(function () {
        readStorage('nameFixPending').then(function (fix) {
          if (!fix) return;
          if ((Date.now() - fix.ts) > 20 * 60000) { removeStorage('nameFixPending'); return; }
          if (deepBusy || busy.deepQueue || forwardingNow.size) return; // 正在扫描/转发 → 下一班再来
          if (fix.lead && leadKeys(fix.lead).length) {
            log('nameFix 超时, 使用兜底名转发:', fix.lead.name);
            var lead = fix.lead;
            removeStorage('nameFixPending');
            proceedSend(lead, leadKeys(lead));
          }
        });
      }, ms);
    });
  }

  function proceedSend(lead, keys) {
    var dedupId = keys.join('|');
    if (forwardingNow.has(dedupId)) return;

    alreadyForwarded(keys).then(function (dup) {
      if (dup) { setHealth('llSkipNote', 'local-forwarded ' + dedupId); log('跳过(本地今天已转发):', lead.name || lead.phone); return; }
      forwardingNow.add(dedupId);
      var msg = { type: 'CHECK_LEAD', data: { phone: lead.phone || '', wechat: lead.wechat || '' } };
      try {
        chrome.runtime.sendMessage(msg, function (res) {
          forwardingNow.delete(dedupId);
          if (chrome.runtime.lastError || !res || res.ok !== true) {
            // OneBot/MagpieBridge 未连接，无法查重 → 不能因此丢线索：
            // 仍照常记录（后台写 CSV）并进入转发队列，OneBot 恢复后自动补发。
            setHealth('llError', 'check_lead: ' + (chrome.runtime.lastError && chrome.runtime.lastError.message || 'res=' + JSON.stringify(res)));
            warn('查重失败(OneBot未连接?), 仍记录并排队转发:', lead.name || lead.phone);
            lead.id = dedupId;
            rememberForwarded(keys);
            if (lead.name) forwardedNames.add(lead.name);
            sendToBackground(lead);
            return;
          }
          if (res.duplicate) {
            log('跳过(CSV历史已转发):', lead.name || lead.phone, res.reason || '');
            rememberForwarded(keys);
            if (lead.name) forwardedNames.add(lead.name);
            return;
          }
          rememberForwarded(keys);
          if (lead.name) forwardedNames.add(lead.name);
          lead.id = dedupId;
          log('检测到新线索:', lead.name, lead.phone, lead.wechat, lead.type);
          sendToBackground(lead);
        });
      } catch (e) {
        forwardingNow.delete(dedupId);
      }
    });
  }

  // ------------------------------------------------------------ 主检测循环
  // 容错原则: 平台"已留资"标签会漏(独立微信号/二维码图片不会被平台识别),
  // 所以【所有最近会话都扫】, 标签只用于排优先级。
  function detectLeads() {
    setHealth('llScanTs', Date.now());
    setHealth('llConvCount', convItems().length);
    try { detectLeadsInner(); }
    catch (e) { setHealth('llError', 'detect: ' + (e && e.message || e)); warn('detectLeads 异常:', e); }
  }
  function detectLeadsInner() {
    var items = convItems();
    if (!items.length) { setHealth('llScanNote', 'no conversationItem'); return; }

    // 懒加载预刷新(2026-09-08): 有线索/未读条目但昵称未就绪 → 先滚动刷新虚拟列表
    // (滚动下去再滚回来, 触发 React 挂载全部条目), 60s 防抖防循环。
    if (!busy.deepQueue && !deepBusy && (Date.now() - lastListRefresh) > 60000
        && countUnnamedLeadItems() > 0) {
      lastListRefresh = Date.now();
      setHealth('llScanNote', 'list refresh for unnamed leads');
      refreshListScroll().then(function () { setTimeout(detectLeads, 600); });
      return;
    }

    // 1) 当前激活窗口(用户在看的 / 刚点开过的): 免费直接检查
    var active = document.querySelector(SEL.convActive);
    if (active) {
      if (!deepBusy) {
        var expectName = itemName(active);
        extractLeadFromOpenedWindow({ expectName: expectName, hoursFilter: null, source: 'active' })
          .then(function (lead) { if (lead) maybeSendLead(lead); });
      }
    }

    // 2) 其余会话分级:
    //    urgent  = 带「已留资」标签 或 有未读 → 近乎即时自动点开(漏客户比打断鼠标严重)
    //    routine = 只是时间新鲜(今天/12h)无标签无未读 → 需较长空闲才复查
    var urgent = [], routine = [];
    items.forEach(function (it) {
      if (it === active) return;
      var nm = itemName(it);
      var key = itemKey(it);
      var unread = unreadCount(it);
      var lt = itemTime(it);
      var marked = itemHasLeadMark(it);
      var recent = !!(lt && (isToday(lt) || isWithinHours(lt, 12)));
      if (!recent && unread <= 0) return;                       // 不新鲜 → 不扫
      if (unread <= 0 && nm && forwardedNames.has(nm)) return;  // 已转发且无新消息
      if (unread <= 0 && clickedThisSession.has(key)) return;   // 本轮点过且没新消息 → 等 10min 清空后再扫
      var e = { item: it, key: key, prio: marked ? 0 : (unread > 0 ? 1 : 2) };
      (marked || unread > 0 ? urgent : routine).push(e);
    });
    setHealth('llQueue', urgent.length + '/' + routine.length);
    if ((urgent.length || routine.length) && !busy.deepQueue && !deepBusy) {
      busy.deepQueue = true;
      runDeepQueue(urgent.concat(routine));
    }
  }

  // 自动点开策略:
  //   带标签/有未读(prio<2) → 只需 2.5s 无鼠标(防抖), 基本即时扫
  //   普通复查(prio=2)      → 需 idleDelayMs 空闲 或 页面在后台, 不打扰客服
  function runDeepQueue(entries) {
    var queue = entries.slice();
    var step = function () {
      var sinceAct = Date.now() - lastUserActivity;
      // 先剔除已失效/条件变化的条目
      while (queue.length && (!queue[0].item || !queue[0].item.isConnected)) queue.shift();
      if (!queue.length) { busy.deepQueue = false; setHealth('llDeepNote', 'queue done'); return; }
      var e = queue[0];
      var gate = (e.prio < 2) ? CONFIG.urgentIdleMs : CONFIG.idleDelayMs;
      if (sinceAct < gate && !document.hidden) {
        setHealth('llDeepNote', 'await idle ' + sinceAct + '/' + gate + ' left ' + queue.length);
        setTimeout(step, 3000);
        return;
      }
      queue.shift();
      clickedThisSession.add(e.key);
      setHealth('llDeepTs', Date.now());
      setHealth('llDeepNote', 'open ' + (e.key || '').slice(0, 20));
      openAndExtract(e.item, { hoursFilter: null, source: 'deep' }).then(function (lead) {
        if (lead) maybeSendLead(lead);
        setTimeout(step, CONFIG.deepScanIntervalMs);
      });
    };
    step();
  }

  // ------------------------------------------------------------ 变化监听
  function observePageChanges() {
    var pending = null;
    var obs = new MutationObserver(function (muts) {
      var hit = false;
      for (var i = 0; i < muts.length && !hit; i++) {
        var m = muts[i];
        var t = m.target;
        if (t && t.nodeType === 1 && t.closest && t.closest(SEL.convItem + ', ' + SEL.msgItem + ', [class*="rc-virtual-list"], [class*="msgTitle"]')) hit = true;
        if (!hit && m.addedNodes) {
          for (var j = 0; j < m.addedNodes.length; j++) {
            var n = m.addedNodes[j];
            if (n.nodeType === 1 && n.matches &&
                (n.matches(SEL.convItem + ', ' + SEL.msgItem + ', [class*="rc-virtual-list"]') ||
                 (n.querySelector && n.querySelector(SEL.convItem + ', ' + SEL.msgItem)))) { hit = true; break; }
          }
        }
      }
      if (!hit) return;
      clearTimeout(pending);
      pending = setTimeout(function () { detectLeads(); }, 600);
    });
    obs.observe(document.body, { childList: true, subtree: true });
  }

  function addStyles() {
    var style = document.createElement('style');
    style.textContent =
      '@keyframes llSlideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }' +
      '@keyframes llSlideOut { from { transform: translateX(0); opacity: 1; } to { transform: translateX(100%); opacity: 0; } }' +
      '.leadslinker-notification { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }';
    document.head.appendChild(style);
  }

  // ------------------------------------------------------------ 发送/通知
  function sendToBackground(lead) {
    try {
      chrome.runtime.sendMessage({ type: 'NEW_LEAD', data: lead }, function (response) {
        if (response && response.success) {
          setHealth('llSent', (Date.now()) + ' ' + (lead.name || lead.phone || lead.wechat));
          log('已入转发队列, leadId:', response.leadId);
          checkNotifEnabled().then(function (on) { if (on) sysNotify(lead); });
          showPageToast('新线索: ' + (lead.name || lead.phone || ''));
        } else {
          warn('发送到后台失败(重试将在下轮, 因未记缓存)');
        }
      });
    } catch (e) { warn('sendMessage 异常:', e && e.message); }
  }

  function checkNotifEnabled() {
    return new Promise(function (resolve) {
      try { chrome.storage.local.get('systemNotificationEnabled', function (r) { resolve(!!r.systemNotificationEnabled); }); }
      catch (e) { resolve(false); }
    });
  }

  function sysNotify(lead) {
    var body = [];
    if (lead.name) body.push('姓名: ' + lead.name);
    if (lead.phone) body.push('电话: ' + lead.phone);
    if (lead.wechat) body.push('微信: ' + lead.wechat);
    if (lead.type) body.push('类型: ' + lead.type);
    try {
      chrome.runtime.sendMessage({ type: 'SHOW_NOTIFICATION', title: '新线索提醒', body: body.join('\n') || '检测到新线索', leadId: lead.id });
    } catch (e) {}
  }

  function showPageToast(msg) {
    try {
      var old = document.querySelector('.leadslinker-notification');
      if (old) old.remove();
      var n = document.createElement('div');
      n.className = 'leadslinker-notification';
      n.textContent = msg;
      n.style.cssText = 'position:fixed;top:20px;right:20px;background:linear-gradient(135deg,#667eea,#764ba2);' +
        'color:#fff;padding:15px 20px;border-radius:8px;z-index:2147483647;box-shadow:0 4px 15px rgba(0,0,0,.3);' +
        'font-size:14px;font-weight:500;max-width:300px;animation:llSlideIn .3s ease-out';
      document.body.appendChild(n);
      setTimeout(function () { n.style.animation = 'llSlideOut .3s ease-out'; setTimeout(function () { n.remove(); }, 300); }, 3000);
    } catch (e) {}
  }

  // ------------------------------------------------------------ 补扫(防漏)
  // 全量扫描策略: clickedThisSession 只是本轮防抖; 定期清空 →
  // 每 10 分钟把"最近的没新未读的会话"再扫一遍(客户可能中途补发联系方式)。
  function startRescan() {
    ['mousemove', 'mousedown', 'keydown', 'wheel', 'touchstart', 'scroll'].forEach(function (ev) {
      document.addEventListener(ev, function () { lastUserActivity = Date.now(); }, { passive: true });
    });
    setInterval(function () { clickedThisSession.clear(); }, 10 * 60 * 1000);
  }

  // ------------------------------------------------------------ 强劲模式(页面侧)
  // 前台时浏览器不对该 tab 节流 → 亚秒级检测无延迟成本; Wake Lock 防息屏。
  // 全程仅 DOM 操作与标准 API: 不模拟鼠标键盘(无 CDP/InputEvent 注入)、不读写剪贴板。
  var TURBO = false;
  var detectTimer = null;
  var wakeLockObj = null;

  function applyTurbo(on) {
    TURBO = !!on;
    CONFIG.detectionInterval = TURBO ? 800 : 3000;
    CONFIG.deepScanIntervalMs = TURBO ? 2500 : 5000;
    CONFIG.urgentIdleMs = TURBO ? 0 : 2500;
    CONFIG.idleDelayMs = TURBO ? 0 : 20000;
    setHealth('llTurbo', TURBO ? 'ON' : 'off');
    if (detectTimer) {
      clearInterval(detectTimer);
      detectTimer = setInterval(detectLeads, CONFIG.detectionInterval);
    }
    manageWakeLock();
    log(TURBO ? '⚡ 强劲模式开启: 亚秒扫描+防息屏' : '强劲模式关闭');
  }

  function manageWakeLock() {
    try {
      if (TURBO && document.visibilityState === 'visible' && navigator.wakeLock && !wakeLockObj) {
        navigator.wakeLock.request('screen').then(function (l) {
          wakeLockObj = l;
          try { l.addEventListener('release', function () { wakeLockObj = null; }); } catch (e) {}
          log('屏幕唤醒锁已获得(防息屏)');
        }).catch(function (e) { log('wakeLock 申请失败:', e && e.message); });
      } else if ((!TURBO || document.hidden) && wakeLockObj) {
        try { wakeLockObj.release(); } catch (e) {}
        wakeLockObj = null;
      }
    } catch (e) {}
  }

  // ------------------------------------------------------------ 初始化
  function init() {
    setHealth('llVersion', LL_VERSION);
    setHealth('llBootTs', Date.now());
    setHealth('llError', '');
    log('初始化 v' + LL_VERSION + ' (全量容错扫描)...');
    try {
      document.addEventListener('visibilitychange', function () {
        manageWakeLock();
        if (document.visibilityState === 'visible') detectLeads(); // 回前台立即补一轮
      });
      loadForwarded().then(function () {
        setHealth('llStorageOk', true);
        try {
          chrome.storage.local.get('turboMode', function (r) { applyTurbo(r.turboMode); });
          chrome.storage.onChanged.addListener(function (ch, area) {
            if (area === 'local' && ch.turboMode) applyTurbo(ch.turboMode.newValue);
          });
        } catch (e) {}
        detectCurrentAccount();
        detectLeads();
        recoverNameFix();
        detectTimer = setInterval(detectLeads, CONFIG.detectionInterval);
        setInterval(detectCurrentAccount, 5000);
        observePageChanges();
        startRescan();
      });
    } catch (e) {
      setHealth('llError', 'init: ' + (e && e.message || e));
      warn('init 异常:', e);
    }
  }

  addStyles();
  try {
    window.addEventListener('error', function (ev) {
      if (ev && ev.message && ev.message.indexOf('LeadsLinker') >= 0) return;
      setHealth('llError', 'winerr: ' + (ev && ev.message || ''));
    });
  } catch (e) {}
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
