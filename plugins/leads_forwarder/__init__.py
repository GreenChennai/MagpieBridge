from .renderer import escape_html, render_html_to_image

# -*- coding: utf-8 -*-
# Plugins/leads_forwarder.py
# LeadsLinker OneBot 插件 - 接收、保存、转发、查询、受理管理

import os
import csv
import difflib
import json
import logging
import asyncio
import re
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("leads_forwarder")

PLUGIN = {
    "name": "leads_forwarder",
    "version": "3.0.0",  # LeadsLinker V3：并入 MagpieBridge 主仓；群名比对委托宿主 target_guard（ADR-0004）
    "description": "LeadsLinker: 线索转发+受理管理+调试通道+CSV历史去重",
    "author": "LeadsLinker",
    "events": ["message"],
    "actions": [
        "send_leads", "test_send_leads", "get_config", "update_config",
        "debug_status", "debug_submit", "debug_poll", "debug_result",
        "debug_fetch", "debug_query", "debug_cancel",
        "leads_check", "blocked_status", "flush_blocked",
    ],
    "monitor_patterns": [
        {"group": "", "user": "", "message": "(^|\\s)(1|0|好的|ok|yes|no|pass|没空|跳过|不接|不要|接|收到|可以)(\\s|$)"},
        {"group": "", "user": "", "message": "^(/ll|/leadslinker)(\\s|$)"},
        {"group": "", "user": "", "message": "^待受理$"},
    ],
    "settings": [
        {"key": "send_mode", "label": "发送格式", "type": "select",
         "options": ["text", "image"], "default": "text",
         "description": "text=文字版, image=图片版"},
        {"key": "reply_handling", "label": "回复受理跟踪", "type": "boolean", "default": True,
         "description": "开=跟踪被@者回复1/0并确认/转单；关=仅转发+@，预记录负责人，不跟踪受理"},
        {"key": "member_pool", "label": "成员库（每行一人，可@）", "type": "textarea", "default": "",
         "description": "所有可 @ 的人"},
        {"key": "group_slots", "label": "群分配配置（JSON，后台面板生成）", "type": "textarea", "default": "",
         "description": "结构化配置：群+时间段+成员+暂停受理；每个时段可加 days=工作日/周末/all（区分平时与周末排班）"},
        {"key": "auto_forward", "label": "超时自动转下一人", "type": "boolean", "default": True,
         "description": "开=被@者超时未回复则自动@下一人；关=仅记录，不自动转"},
        {"key": "groups", "label": "目标群（旧格式，每行一个群名）", "type": "textarea", "default": "",
         "description": "旧版兼容：每行一个群名"},
        {"key": "slots", "label": "时间槽（旧格式）", "type": "textarea", "default": "",
         "description": "旧版兼容：群名|HH:MM-HH:MM|成员1,成员2"},
        {"key": "blocked", "label": "阻塞时段（旧格式）", "type": "textarea", "default": "",
         "description": "旧版兼容：HH:MM-HH:MM"},
        {"key": "members", "label": "成员列表（旧格式）", "type": "textarea", "default": "",
         "description": "旧版兼容：每行一个人名"},
        {"key": "weekdayOrder", "label": "工作日 @顺序（旧格式）", "type": "textarea", "default": "",
         "description": "旧版兼容"},
        {"key": "weekendOrder", "label": "周末 @顺序（旧格式）", "type": "textarea", "default": "",
         "description": "旧版兼容"},
        {"key": "reply_timeout", "label": "回复超时（分钟）", "type": "number", "default": 5,
         "description": "被 @ 者超时未回复受理，自动转发给下一个人"},
        {"key": "debug_enabled", "label": "调试通道（供AI分析页面）", "type": "boolean", "default": True,
         "description": "开=扩展通过本机3000端口上报抖音来客页面快照供 AI 分析，仅本机可访问；生产测试完建议关闭"},
    ],
}

# Injected by MagpieBridge PluginManager at load time (后台插件设置)。
# 优先级高于本地 ~/LeadsLinker/config.json；保存时也会写回本地文件保持兼容。
PLUGIN_SETTINGS = {}

LEADS_DIR = Path(os.path.expanduser("~")) / "LeadsLinker" / "leads"
DAILY_DIR = LEADS_DIR / "daily"
LEADS_DIR.mkdir(parents=True, exist_ok=True)
DAILY_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = Path(os.path.expanduser("~")) / "LeadsLinker" / "config.json"
LEAD_COUNT_PATH = LEADS_DIR / ".lead_count"
SERIAL_PATH = LEADS_DIR / ".serial"
ALLOC_STATE_PATH = LEADS_DIR / ".alloc_state.json"
QUEUE_PATH = LEADS_DIR / ".blocked_queue.json"

CSV_HEADERS = ["序号", "时间", "账号", "姓名", "电话", "微信号", "备注", "类型", "页面", "消息时间", "头像"]

# 类型标签中文化：上游来客 type 可能是英文（lead/AD/biz），徽章不允许透传英文。
# key 统一小写匹配；命中后徽章显示中文，未命中（已是中文或未知）保留原值兜底。
_TYPE_CN = {
    "lead": "线索",
    "leads": "线索",
    "new": "新线索",
    "ad": "广告",
    "ads": "广告",
    "advert": "广告",
    "advertisement": "广告",
    "biz": "经营线索",
    "business": "经营线索",
    "cooperation": "经营线索",
    "other": "其他",
}

COMMAND_PREFIXES = ("/ll", "/leadslinker")

ACCEPT_KEYWORDS = ("1", "好的", "ok", "接", "收到", "yes", "可以")
REJECT_KEYWORDS = ("0", "没空", "pass", "不接", "跳过", "不要", "no")

# ---------------------------------------------------------------- 在途发送去重
# v4.8.4: send_leads 改为"秒回受理 + 后台任务发送"后，同一线索在发送完成前
# 仍可能被重复 POST（弹窗重发/扩展重启/脚本重试）。这里记录**正在后台发送**
# 的线索 key（p:电话 / w:微信号），重复请求直接按已受理返回，不再重新入队。
_inflight_keys: set = set()
_inflight_lock = None


def _get_inflight_lock():
    global _inflight_lock
    if _inflight_lock is None:
        _inflight_lock = asyncio.Lock()
    return _inflight_lock


def _lead_dedup_keys(lead):
    out = []
    phone = str((lead or {}).get("phone") or "").strip()
    wechat = str((lead or {}).get("wechat") or "").strip()
    if phone:
        out.append("p:" + phone)
    if wechat:
        out.append("w:" + wechat)
    return out


async def _acquire_inflight(keys):
    """尝试占用在途槽位。返回 True=本次为重复请求(已在途, 应拒绝入队)。"""
    if not keys:
        return False
    async with _get_inflight_lock():
        if any(k in _inflight_keys for k in keys):
            return True
        _inflight_keys.update(keys)
    return False


async def _release_inflight(keys):
    if not keys:
        return
    async with _get_inflight_lock():
        for k in keys:
            _inflight_keys.discard(k)


# 受理/拒收关键词判定必须"整词匹配"，不能子串匹配。子串匹配的误判：
#   - "不接"/"不要" 含 "接"，且 accept 分支在前 → 拒绝被误记为承接（P1）
#   - "12345" 含 "1"、"100元" 含 "0"、正文含 "好的/可以" → 随意触发受理流程
# Python re 在 Unicode 模式下 \w 视 CJK 汉字为单词字符，故 (?<!\w)/(?!\w)
# 能挡住 "不接" 里的 "接"、"第1个" 里的 "1"（前后紧贴汉字/字母时不命中）。
def _compile_keywords(keywords):
    return re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(k) for k in keywords) + r")(?!\w)",
        re.IGNORECASE,
    )


_ACCEPT_RE = _compile_keywords(ACCEPT_KEYWORDS)
_REJECT_RE = _compile_keywords(REJECT_KEYWORDS)


def _classify_reply(msg_lower):
    """整词分类回复意图。拒收优先：消息同时含拒收词与接收词时（如
    "不接 1"），宁可判拒收也不把拒收误记成承接。"""
    is_reject = bool(_REJECT_RE.search(msg_lower))
    is_accept = (not is_reject) and bool(_ACCEPT_RE.search(msg_lower))
    return is_accept, is_reject


_pending_leads = {}
_pending_lock = asyncio.Lock()
_timeout_task_started = False
_current_engine = None  # lazily set by actions so the blocked-queue flusher can send


def _ensure_timeout_task():
    """Start the background timeout-checker exactly once.

    The plugin has no dedicated "on load" lifecycle hook in older MagpieBridge
    versions, so we lazily schedule the checker the first time the plugin is
    used.  `_check_timeouts` forwards leads that were not accepted in time.
    """
    global _timeout_task_started
    if _timeout_task_started:
        return
    _timeout_task_started = True
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_check_timeouts())
        loop.create_task(_check_blocked())
        logger.info("受理超时检查 + 阻塞队列刷新任务已启动")
    except Exception as e:
        logger.warning("启动受理超时检查失败: %s", e)


def _split_lines(value):
    """Split a textarea-style setting into a non-empty list."""
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if value is None:
        return []
    return [line.strip() for line in str(value).splitlines() if line.strip()]


def _backend_config():
    """Build config from MagpieBridge 后台插件设置 (PLUGIN_SETTINGS)."""
    settings = globals().get("PLUGIN_SETTINGS") or {}
    if not settings:
        return None
    groups, members, schedule = _build_groups(settings)
    return {
        "groups": groups,
        "members": members,
        "schedule": schedule,
        "blocked": _parse_blocked(settings.get("blocked", "")),
        "reply_handling": bool(settings.get("reply_handling", True)),
        "auto_forward": bool(settings.get("auto_forward", True)),
        "reply_timeout": int(settings.get("reply_timeout") or 5),
    }


def _parse_blocked(text):
    """Parse blocked windows: each line 'HH:MM-HH:MM'."""
    out = []
    for line in _split_lines(text):
        m = re.match(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", line.strip())
        if m:
            out.append({"start": f"{int(m.group(1)):02d}:{m.group(2)}",
                        "end": f"{int(m.group(3)):02d}:{m.group(4)}"})
    return out


def _build_groups(settings):
    """Build per-group config with time slots from PLUGIN_SETTINGS.

    If `slots` is set (each line `群名|HH:MM-HH:MM|成员1,成员2,...`), parse it.
    Otherwise fall back to the legacy groups/members/schedule format.
    """
    slots_text = settings.get("slots", "")
    group_names = _split_lines(settings.get("groups", ""))
    members = _split_lines(settings.get("members", ""))
    member_pool = _split_lines(settings.get("member_pool", ""))
    if not member_pool:
        member_pool = members
    schedule = {
        "weekdayOrder": _split_lines(settings.get("weekdayOrder", "")),
        "weekendOrder": _split_lines(settings.get("weekendOrder", "")),
    }

    # 1) structured config from the rich settings panel (group_slots = JSON)
    gs_text = settings.get("group_slots", "")
    if str(gs_text).strip():
        try:
            data = json.loads(gs_text)
            if isinstance(data, list):
                groups = []
                for i, g in enumerate(data):
                    if not isinstance(g, dict):
                        continue
                    slots = []
                    for s in (g.get("slots") or []):
                        if not isinstance(s, dict):
                            continue
                        slots.append({
                            "start": str(s.get("start", "00:00")),
                            "end": str(s.get("end", "23:59")),
                            "members": [str(m) for m in (s.get("members") or []) if str(m)],
                            "pause": bool(s.get("pause", False)),
                            "days": str(s.get("days", "all") or "all"),
                        })
                    groups.append({"id": i + 1, "name": str(g.get("name", "")),
                                   "slots": sorted(slots, key=lambda s: s["start"])})
                return groups, member_pool, schedule
        except Exception as e:
            logger.info(f"[LeadsLinker] group_slots 解析失败: {e}")

    if str(slots_text).strip():
        by_group = {}
        for line in str(slots_text).splitlines():
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 3:
                continue
            gname, timerange, members_str = parts[0], parts[1], parts[2]
            # 可选第4段：工作日/周末/all（缺省=每天）
            days = "all"
            if len(parts) >= 4 and parts[3]:
                days = parts[3].lower()
            m = re.match(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", timerange)
            if not m or not gname:
                continue
            start = f"{int(m.group(1)):02d}:{m.group(2)}"
            end = f"{int(m.group(3)):02d}:{m.group(4)}"
            members_list = [x.strip() for x in members_str.split(",") if x.strip()]
            by_group.setdefault(gname, []).append({"start": start, "end": end, "members": members_list, "days": days})
        groups = [{"id": i + 1, "name": g, "slots": sorted(v, key=lambda s: s["start"])}
                  for i, (g, v) in enumerate(by_group.items())]
        return groups, members, schedule

    # legacy fallback: one group = one 00:00-23:59 slot with all members
    order = schedule.get("weekdayOrder") or members
    groups = [{"id": i + 1, "name": g, "slots": [{"start": "00:00", "end": "23:59", "members": order}]}
              for i, g in enumerate(group_names)]
    return groups, members, schedule


def _load_config():
    # 1) 后台插件设置优先（MagpieBridge 控制台配置）
    backend = _backend_config()
    if backend is not None:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(backend, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.info(f"[LeadsLinker] 同步本地配置失败: {e}")
        return backend

    # 2) 回退本地文件（浏览器插件旧配置）
    if CONFIG_PATH.exists():
        try:
            return json.load(open(CONFIG_PATH, "r", encoding="utf-8"))
        except Exception as e:
            logger.info(f"[LeadsLinker] 加载配置失败: {e}")
    return {"groups": [], "members": [], "schedule": {"weekdayOrder": [], "weekendOrder": []},
            "blocked": [], "reply_handling": True, "reply_timeout": 5}


# ---------------------------------------------------------------- time / slots
def _parse_hm(s):
    try:
        h, m = str(s).split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 0


def _in_slot(now, start, end):
    """True if `now` is within [start, end], supporting overnight windows."""
    now_m = now.hour * 60 + now.minute
    s, e = _parse_hm(start), _parse_hm(end)
    if s <= e:
        return s <= now_m <= e
    return now_m >= s or now_m <= e  # crosses midnight


def _is_weekend(now=None) -> bool:
    """周六/周日 = 周末（0=周一 ... 5=周六, 6=周日）。"""
    now = now or datetime.now()
    return now.weekday() >= 5


def _slot_days_match(slot, now=None) -> bool:
    """时段是否适用于今天（支持 工作日/周末 区分）。

    时段可带 ``days`` 字段：
      - "weekday" / "工作日"  → 仅周一~周五生效
      - "weekend" / "周末"    → 仅周六~周日生效
      - "all" / 缺省          → 每天生效（向后兼容旧配置）
    """
    days = str(slot.get("days") or "all").strip().lower()
    if days in ("weekday", "工作日", "workday", "mon-fri"):
        return not _is_weekend(now)
    if days in ("weekend", "周末", "sat-sun"):
        return _is_weekend(now)
    return True


def _is_blocked_now(config, now=None):
    now = now or datetime.now()
    # explicit blocked windows (the older "22:00-09:00" format) still honored
    for b in config.get("blocked", []) or []:
        if b.get("start") and b.get("end") and _in_slot(now, b["start"], b["end"]):
            return True
    # 全局阻塞仅当「当前没有任何一个未暂停(可用)时段」时才成立。
    # 旧的"任一 pause:true 时段活跃即全局阻塞"会把「Green_Chennai 全天可接 +
    # 其他成员暂停」误判成整组不可发（线索被全部暂存不发送 = 用户看到的
    # "新线索没转发"）。只要存在 pause:false 且当前有效（含 工作日/周末 过滤）
    # 的时段 → 不阻塞，由 _select_target 从可用时段里挑人。
    for g in config.get("groups", []) or []:
        for slot in g.get("slots", []) or []:
            if (not slot.get("pause")
                    and _slot_days_match(slot, now)
                    and _in_slot(now, slot.get("start", "00:00"), slot.get("end", "23:59"))):
                return False
    return True


def _current_pool(group_cfg, now):
    """Members available for a group at the current time (from its slots).

    只考虑适用于今天（工作日/周末过滤后的）时段。
    """
    for slot in group_cfg.get("slots", []) or []:
        if slot.get("pause"):
            continue  # 暂停受理时段不分配人
        if _slot_days_match(slot, now) and _in_slot(now, slot.get("start", "00:00"), slot.get("end", "23:59")):
            return [m for m in slot.get("members", []) if m]
    return []


# ---------------------------------------------------------------- alloc state
def _load_alloc_state():
    if ALLOC_STATE_PATH.exists():
        try:
            return json.loads(ALLOC_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"global": 0, "group_member": {}}


def _save_alloc_state(state):
    try:
        ALLOC_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _next_member(state, group_name, pool, skip_member=None):
    cand = [m for m in pool if m != skip_member]
    if not cand:
        return ""
    idx = state["group_member"].get(group_name, 0)
    member = cand[idx % len(cand)]
    state["group_member"][group_name] = idx + 1
    return member


def _select_target(skip_member=None):
    """Allocate the next (group, member) by time-slot.

    Round-robins across groups, and within each group's active time slot
    round-robins its members.  Returns ("", "") when no group is active now.
    """
    config = _load_config()
    groups = [g for g in config.get("groups", [])
              if isinstance(g, dict) and g.get("name") and g.get("slots")]
    if not groups:
        return "", ""
    now = datetime.now()
    state = _load_alloc_state()
    g = state["global"]
    for _ in range(len(groups)):
        grp = groups[g % len(groups)]
        pool = _current_pool(grp, now)
        if pool:
            state["global"] = g + 1
            member = _next_member(state, grp["name"], pool, skip_member)
            _save_alloc_state(state)
            return grp["name"], member
        g += 1
    state["global"] = g + 1
    _save_alloc_state(state)
    return "", ""


# ---------------------------------------------------------------- blocked queue
def _enqueue_blocked(items):
    """Append (serial, lead) items to the blocked queue (persistent)."""
    try:
        q = json.loads(QUEUE_PATH.read_text(encoding="utf-8")) if QUEUE_PATH.exists() else []
    except Exception:
        q = []
    q.extend(items)
    try:
        QUEUE_PATH.write_text(json.dumps(q, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _drain_blocked(queue):
    """Pop everything from the blocked queue and return (leftover, popped)."""
    try:
        q = json.loads(queue.read_text(encoding="utf-8")) if queue.exists() else []
    except Exception:
        q = []
    if not q:
        return q, []
    queue.write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
    return [], q


def _get_reply_timeout():
    config = _load_config()
    return config.get("reply_timeout", 5)


def _get_send_mode():
    settings = globals().get("PLUGIN_SETTINGS") or {}
    return settings.get("send_mode", "text")


def _reply_handling_enabled():
    settings = globals().get("PLUGIN_SETTINGS") or {}
    if "reply_handling" in settings:
        return bool(settings.get("reply_handling", True))
    config = _load_config()
    return bool(config.get("reply_handling", True))


def _auto_forward_enabled():
    """Auto @ the next person when the assigned member doesn't reply in time."""
    settings = globals().get("PLUGIN_SETTINGS") or {}
    if "auto_forward" in settings:
        return bool(settings.get("auto_forward", True))
    config = _load_config()
    return bool(config.get("auto_forward", True))


def _get_lead_count():
    if LEAD_COUNT_PATH.exists():
        try:
            return int(LEAD_COUNT_PATH.read_text(encoding="utf-8").strip())
        except Exception:
            pass
    return 0


def _save_lead_count(count):
    LEAD_COUNT_PATH.write_text(str(count), encoding="utf-8")


def _next_serial():
    now = datetime.now()
    month_prefix = now.strftime("L-%Y%m-")

    if SERIAL_PATH.exists():
        try:
            last_line = SERIAL_PATH.read_text(encoding="utf-8").strip()
            if last_line.startswith(month_prefix):
                seq = int(last_line.split("-")[-1]) + 1
            else:
                seq = 1
        except Exception:
            seq = 1
    else:
        seq = 1

    serial = f"{month_prefix}{seq:04d}"
    SERIAL_PATH.write_text(serial, encoding="utf-8")
    return serial



def _lead_to_row(lead, serial=""):
    return [
        serial,
        lead.get("timestamp", ""),
        lead.get("account", ""),
        lead.get("name", ""),
        lead.get("phone", ""),
        lead.get("wechat", ""),
        lead.get("note", ""),
        lead.get("type", ""),
        lead.get("pageUrl", ""),
        lead.get("messageTime", ""),
        "有" if lead.get("avatar") else "无",
    ]


def _append_csv(csv_path, row):
    file_exists = csv_path.exists()
    with open(csv_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(CSV_HEADERS)
        writer.writerow(row)


def _save_lead(lead):
    serial = _next_serial()
    now = datetime.now()
    month_str = now.strftime("%Y-%m")
    day_str = now.strftime("%Y-%m-%d")

    monthly_path = LEADS_DIR / f"leads_{month_str}.csv"
    daily_path = DAILY_DIR / f"leads_{day_str}.csv"

    row = _lead_to_row(lead, serial)
    _append_csv(monthly_path, row)
    _append_csv(daily_path, row)

    return serial, monthly_path, daily_path


def _read_csv_all(csv_path):
    if not csv_path.exists():
        return []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader)


def _format_lead_detail(row):
    lines = []
    serial = row.get("序号", "")
    name = row.get("姓名", "")
    phone = row.get("电话", "")
    wechat = row.get("微信号", "")
    note = row.get("备注", "")
    lead_type = row.get("类型", "")
    msg_time = row.get("消息时间", "") or row.get("时间", "")

    if serial:
        lines.append(f"序号: {serial}")
    if name:
        lines.append(f"姓名: {name}")
    if phone:
        lines.append(f"电话: {phone}")
    if wechat:
        lines.append(f"微信: {wechat}")
    if note:
        lines.append(f"备注: {note}")
    if lead_type:
        lines.append(f"类型: {lead_type}")
    if msg_time:
        lines.append(f"时间: {msg_time}")

    return "\n".join(lines)


def _query_by_serial(serial, month=None):
    if month:
        csv_path = LEADS_DIR / f"leads_{month}.csv"
        rows = _read_csv_all(csv_path)
    else:
        rows = []
        for csv_file in sorted(LEADS_DIR.glob("leads_????-??.csv"), reverse=True):
            rows.extend(_read_csv_all(csv_file))

    for row in rows:
        if row.get("序号", "") == serial:
            return row
    return None


def _month_summary(month=None):
    if not month:
        month = datetime.now().strftime("%Y-%m")

    monthly_path = LEADS_DIR / f"leads_{month}.csv"
    rows = _read_csv_all(monthly_path)

    if not rows:
        return f"月份 {month} 暂无数据"

    total = len(rows)
    by_type = {}
    by_day = {}
    for row in rows:
        t = row.get("类型", "未知")
        by_type[t] = by_type.get(t, 0) + 1
        msg_time = row.get("消息时间", "") or row.get("时间", "")
        day = msg_time[:10] if len(msg_time) >= 10 else "未知"
        by_day[day] = by_day.get(day, 0) + 1

    lines = [f"📊 {month} 月度统计", f"总线索数: {total}", ""]
    lines.append("按类型:")
    for t, c in sorted(by_type.items(), key=lambda x: -x[1]):
        lines.append(f"  {t}: {c} 条")

    lines.append("")
    lines.append("按日期 (最近7天):")
    for day in sorted(by_day.keys(), reverse=True)[:7]:
        lines.append(f"  {day}: {by_day[day]} 条")

    return "\n".join(lines)


def _day_summary(day=None):
    if not day:
        day = datetime.now().strftime("%Y-%m-%d")

    daily_path = DAILY_DIR / f"leads_{day}.csv"
    rows = _read_csv_all(daily_path)

    if not rows:
        return f"日期 {day} 暂无数据"

    total = len(rows)
    by_type = {}
    for row in rows:
        t = row.get("类型", "未知")
        by_type[t] = by_type.get(t, 0) + 1

    lines = [f"📊 {day} 每日统计", f"总线索数: {total}", ""]
    for t, c in sorted(by_type.items(), key=lambda x: -x[1]):
        lines.append(f"  {t}: {c} 条")

    if total <= 10:
        lines.append("")
        lines.append("线索列表:")
        for row in rows:
            serial = row.get("序号", "?")
            name = row.get("姓名", "")
            phone = row.get("电话", "")
            lines.append(f"  {serial} | {name} | {phone}")

    return "\n".join(lines)


async def _send_state(engine, state_method, bool_method, *args, **kw):
    """调用 ui_engine 的三态发送接口(sent/failed/uncertain)。

    旧版 MagpieBridge 核心没有 *_state 方法(只有返回 bool 的旧接口)时降级为
    两态，保证插件单独热更新也不会炸。
    """
    fn = getattr(engine, state_method, None)
    if fn is not None:
        return await fn(*args, **kw)
    return "sent" if await getattr(engine, bool_method)(*args, **kw) else "failed"


async def _send_pure_at(engine, group_name, member_name):
    """纯 @ 提醒（v4.8.3 起：@ 消息不再附带"受理提醒"正文）。

    微信 UI 的 @ 发送必须让输入框相对空白状态有像素变化才能通过
    ``_ensure_input_content`` 差分校验，因此正文传**单个普通空格**；
    微信发送时自动忽略尾随空白，群里最终只显示 ``@名字``。

    v4.8.4: 返回发送状态(sent/failed/uncertain)，由 _send_lead_batch 决定重试。
    """
    return await _send_state(engine, "send_at_message_state", "send_at_message",
                             group_name, member_name, " ")


async def _send_lead_batch(engine, leads_data, saved_serials, group_name, member_name):
    """统一发送一批线索；返回 True = 所有步骤都**确认**已发出。

    发送模式（后台设置 send_mode）：
    - image（v4.6.0：一次一位客户）: 每条线索单独渲染一张单卡图逐张发送
             → 联系方式文字（_build_contact_message，整批合并一条，仅 📱/💬）
             → 纯 @人 提醒（_send_pure_at，不带正文，v4.8.3）
            单条图片渲染失败自动降级为文字版补发。
    - text:  ① 主要文字部分（_build_text_message，完整详情）
             → ② 纯 @人 提醒（_send_pure_at，不带正文，v4.8.3）
    图片渲染失败自动降级为文字版。

    v4.8.4 重试语义（修"失败后整批循环重发"）：每一步**独立**判定，只有
    明确 "failed"（确认没发出去）才单独重试该步一次；"uncertain"（UI 操作
    超时、消息可能稍后才发出）**绝不重发** —— 旧版忽略所有步骤返回值，任一步
    失败靠上游整批重来，把已成功发出的图片一遍遍刷屏。
    """
    target = {"group": group_name, "member": member_name}
    all_sent = True

    async def _step(label, fn):
        nonlocal all_sent
        st = await fn()
        if st == "failed":
            logger.info("[LeadsLinker] %s 确认未发出，单独重试这一步", label)
            st = await fn()
        if st != "sent":
            all_sent = False
            logger.error("[LeadsLinker] %s 最终未确认送达(状态=%s)，跳过不重发已成功步骤", label, st)
        return st

    send_mode = _get_send_mode()
    if send_mode == "image":
        failed_leads, failed_serials = [], []
        try:
            # 一次一位客户（v4.6.0）：每条线索单独渲染一张单卡图，逐张发送；
            # 不再把整批线索拼成一张纵向超长图。联系方式文字与 @提醒仍整批合并各发一条。
            for i, lead in enumerate(leads_data):
                serial = saved_serials[i] if i < len(saved_serials) else None
                one_html = _build_image_content([lead], target,
                                                serials=[serial] if serial else None)
                one_bytes = await render_html_to_image(one_html)
                if one_bytes:
                    _b = one_bytes
                    await _step(f"图片卡片[{serial or lead.get('name') or lead.get('phone')}]",
                                lambda: _send_state(engine, "send_image_bytes_state",
                                                    "send_image_bytes", group_name, _b))
                else:
                    failed_leads.append(lead)
                    failed_serials.append(serial)
            # ② 联系方式文字（整批合并一条，仅列存在的 📱/💬，v4.8.3 去掉序号/姓名）
            contact_msg = _build_contact_message(leads_data)
            if contact_msg.strip():
                await _step("联系方式文字",
                            lambda: _send_state(engine, "send_text_state", "send_text",
                                                group_name, contact_msg))
            # ③ 纯 @人 提醒（不附"受理提醒"正文，避免刷屏；详情/受理指引已在图上）
            await _step(f"@{member_name} 提醒",
                        lambda: _send_pure_at(engine, group_name, member_name))
            # 图片渲染失败的单条 → 文字版补发（不重复发联系方式与 @）
            if failed_leads:
                logger.info("[LeadsLinker] %d 条图片渲染为空, 改发文字版", len(failed_leads))
                text_msg = _build_text_message(failed_leads, target, serials=failed_serials)
                await _step("图片降级文字版",
                            lambda: _send_state(engine, "send_text_state", "send_text",
                                                group_name, text_msg))
            return all_sent
        except Exception as e:
            logger.info(f"[LeadsLinker] 发送图片消息失败, 降级文字版: {e}")
            all_sent = False
    # 文字版：① 主要文字 → ② 纯 @人 提醒
    text_msg = _build_text_message(leads_data, target, serials=saved_serials)
    await _step("文字线索",
                lambda: _send_state(engine, "send_text_state", "send_text",
                                    group_name, text_msg))
    await _step(f"@{member_name} 提醒",
                lambda: _send_pure_at(engine, group_name, member_name))
    return all_sent


async def _forward_leads(engine, leads_data, saved_serials, group_name, member_name):
    """Send one batch of leads to a group (text or image per send_mode)."""
    return await _send_lead_batch(engine, leads_data, saved_serials, group_name, member_name)


async def _forward_task(engine, leads_data, saved_serials, group_name, member_name,
                        keys, record_history=True):
    """后台发送任务（v4.8.4：send_leads 秒回后由它真正驱动微信串行发送）。

    - 发送尝试结束后写历史 CSV（无论部分步骤是否送达——图片已经进群，重发
      整批只会二次刷屏；未送达步骤已有 alerter 邮件告警给运维）
    - 受理跟踪仍按 reply_handling 开关
    - 结束时释放在途去重 key
    """
    try:
        all_sent = False
        try:
            all_sent = await _send_lead_batch(engine, leads_data, saved_serials,
                                              group_name, member_name)
        finally:
            if record_history:
                try:
                    for lead in leads_data:
                        _history_record(lead)
                except Exception as e:
                    logger.info(f"[LeadsLinker] 写历史CSV失败: {e}")
        if not all_sent:
            logger.error("[LeadsLinker] 本批存在未确认送达步骤，已写历史防整批重发；"
                         "请查收失败告警邮件/日志人工确认")
        if _reply_handling_enabled():
            for i, lead in enumerate(leads_data):
                serial = saved_serials[i] if i < len(saved_serials) else f"unknown_{i}"
                pending_id = f"{serial}_{datetime.now().timestamp()}"
                await _add_pending_lead(pending_id, lead, serial, group_name, member_name, engine)
    except Exception:
        logger.exception("[LeadsLinker] 后台转发任务异常")
    finally:
        await _release_inflight(keys)


def _build_contact_message(leads):
    """联系方式文字块（图片模式第②步，v4.8.3 起只列可复制的联系方式）。

    图片卡片已展示序号/姓名/时间等详情，本条只补可复制点按的字段：
    📱 手机号 / 💬 微信号 —— 有哪个发哪个，都有就一起发，都没有返回空串
    （调用方已判空不发）。
    """
    lines = ["📋 联系方式"]
    for i, lead in enumerate(leads, 1):
        phone = lead.get("phone")
        wechat = lead.get("wechat")
        if not phone and not wechat:
            continue
        if len(leads) > 1:
            lines.append(f"── 线索 {i} ──")
        if phone:
            lines.append(f"📱 {phone}")
        if wechat:
            lines.append(f"💬 微信号: {wechat}")
    if len(lines) == 1:
        return ""  # 本批全部无联系方式 → 不产生消息
    return "\n".join(lines)


def _build_text_message(leads, target, serials=None):
    """文字模式第①步：主要文字部分（完整线索详情）。

    第②步为纯 @ 提醒（_send_pure_at），不再附"受理提醒"正文（v4.8.3）。
    """
    lines = ["【新线索提醒】"]
    lines.append(f"发送时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    for i, lead in enumerate(leads, 1):
        serial = serials[i - 1] if serials and i <= len(serials) else ""
        if len(leads) > 1:
            lines.append(f"--- 线索 {i} ---")
        if serial:
            lines.append(f"序号: {serial}")
        if lead.get("messageTime"):
            lines.append(f"留资时间: {lead['messageTime']}")
        if lead.get("name"):
            lines.append(f"姓名: {lead['name']}")
        if lead.get("phone"):
            lines.append(f"电话: {lead['phone']}")
        elif lead.get("wechat"):
            lines.append(f"微信: {lead['wechat']}")
        if lead.get("note"):
            lines.append(f"备注: {lead['note']}")
        if lead.get("type"):
            lines.append(f"类型: {lead['type']}")
        lines.append("")

    return "\n".join(lines)


def _build_image_content(leads, target, serials=None):
    group = target.get("group", "")
    member = target.get("member", "")
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    reply_timeout = _get_reply_timeout()
    n_leads = len(leads)
    title_suffix = f"（{n_leads}条）" if n_leads > 1 else ""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  font-family: 'Microsoft YaHei', 'PingFang SC', 'Segoe UI', sans-serif;
  width: 520px;
  padding: 18px;
  background: linear-gradient(170deg, #eef0f8 0%, #e7eaf6 55%, #f0f1f9 100%);
}}
.card {{
  background: #ffffff;
  border-radius: 24px;
  overflow: hidden;
  box-shadow: 0 20px 50px -14px rgba(60, 66, 180, 0.18), 0 6px 18px rgba(60, 66, 180, 0.06);
}}
/* ===== 顶栏：品牌渐变 + 柔光装饰 ===== */
.card-header {{
  background: linear-gradient(125deg, #5b5fe8 0%, #7c5cf0 48%, #b14ee0 100%);
  color: #ffffff;
  padding: 20px 22px;
  display: flex;
  align-items: center;
  gap: 13px;
  position: relative;
  overflow: hidden;
}}
.card-header::before {{
  content: '';
  position: absolute;
  right: -46px;
  top: -58px;
  width: 190px;
  height: 190px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(255,255,255,0.20), transparent 68%);
}}
.card-header::after {{
  content: '';
  position: absolute;
  right: 52px;
  bottom: -26px;
  width: 72px;
  height: 72px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(255,255,255,0.13), transparent 70%);
}}
.card-header .icon {{
  width: 44px;
  height: 44px;
  border-radius: 14px;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.20);
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.28);
}}
.card-header .icon svg {{ width: 23px; height: 23px; }}
.card-header .title-group {{ flex: 1; min-width: 0; position: relative; z-index: 1; }}
.card-header h2 {{
  font-size: 18px;
  font-weight: 800;
  letter-spacing: 0.5px;
}}
.card-header .subtitle {{
  font-size: 12px;
  margin-top: 5px;
  opacity: 0.86;
  letter-spacing: 0.2px;
}}
/* ===== 线索卡主体 ===== */
.lead-card {{
  padding: 20px 22px 18px;
}}
.lead-card + .lead-card {{
  border-top: 1px solid #f2f0fb;
}}
.lead-top {{
  display: flex;
  align-items: center;
  gap: 13px;
}}
.avatar-wrap {{
  width: 52px;
  height: 52px;
  border-radius: 50%;
  overflow: hidden;
  flex-shrink: 0;
  background: linear-gradient(140deg, #6d6cf6 0%, #9a6bf2 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 6px 14px rgba(108, 99, 245, 0.28);
}}
.avatar-wrap img {{
  width: 100%;
  height: 100%;
  object-fit: cover;
}}
.avatar-placeholder {{
  font-size: 23px;
  color: #ffffff;
  font-weight: 700;
  line-height: 1;
}}
.lead-name-group {{ flex: 1; min-width: 0; }}
.lead-name {{
  font-size: 17px;
  font-weight: 800;
  color: #1c2142;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.lead-meta {{
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 7px;
  min-height: 18px;
}}
.lead-serial {{
  display: inline-block;
  font-size: 11px;
  font-weight: 600;
  color: #6d5bf0;
  background: #f0edfe;
  padding: 2px 8px;
  border-radius: 6px;
  flex-shrink: 0;
}}
.lead-time {{
  font-size: 13px;
  color: #64748b;
  display: flex;
  align-items: center;
  gap: 5px;
}}
.lead-time::before {{
  content: '';
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #a78bfa;
  display: inline-block;
}}
.lead-type-badge {{
  display: inline-block;
  padding: 4px 12px;
  border-radius: 999px;
  font-size: 11.5px;
  font-weight: 600;
  letter-spacing: 0.3px;
  flex-shrink: 0;
  margin-left: 6px;
  align-self: flex-start;
}}
.badge-lead {{ background: #e4f9ec; color: #0d9f54; }}
.badge-ad {{ background: #e5efff; color: #2b6de8; }}
.badge-biz {{ background: #fdf1da; color: #d97b06; }}
.badge-unknown {{ background: #f0f1f5; color: #7b8192; }}
/* ===== 联系信息区：浅渐变块 + 彩色圆底图标行 ===== */
.contact-block {{
  margin-top: 14px;
  background: linear-gradient(135deg, #f7f8ff 0%, #f8f4ff 100%);
  border: 1px solid #ece9fa;
  border-radius: 16px;
  padding: 6px 14px;
}}
.contact-row {{
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 11px 0;
}}
.contact-row + .contact-row {{
  border-top: 1px dashed #e3e0f4;
}}
.contact-ico {{
  width: 30px;
  height: 30px;
  border-radius: 9px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}}
.contact-ico svg {{ width: 16px; height: 16px; }}
.ico-qr {{ background: #f1edff; color: #7c3aed; }}
.ico-phone {{ background: #e9edff; color: #4f46e5; }}
.ico-wechat {{ background: #e2f7ec; color: #07c160; }}
.ico-info {{ background: #f0f2f7; color: #7b8192; }}
.contact-label {{
  color: #98a0b3;
  font-size: 12px;
  width: 34px;
  flex-shrink: 0;
}}
.contact-value {{
  flex: 1;
  min-width: 0;
  font-size: 15px;
  color: #2c3058;
}}
.contact-value.phone {{
  font-size: 19px;
  font-weight: 800;
  letter-spacing: 1.5px;
  color: #3f3fd6;
  word-break: break-all;
}}
.contact-value.wechat {{
  font-size: 16px;
  font-weight: 800;
  color: #0aa957;
  word-break: break-all;
}}
/* ===== 二维码区：横向三段 [竖排"二/维/码"标签] | [QR图] | [三行文案] (v4.8.2) ===== */
.qr-row {{
  display: flex;
  align-items: center;
  gap: 14px;
  flex: 1;
  min-width: 0;
  padding: 4px 0 2px;
}}
.qr-tag {{
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: #98a0b3;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 1px;
  line-height: 1;
  padding: 2px 4px;
  flex-shrink: 0;
  /* 微竖条装饰 */
  border-left: 2px solid #d8d4f0;
  border-right: 2px solid #d8d4f0;
  border-radius: 2px;
}}
.qr-tag span {{
  display: block;
}}
.qr-img {{
  width: 116px;                 /* 横向三段：QR 图比纯竖排略小一点，给左右两列让位 */
  height: 116px;
  border-radius: 12px;
  background: #ffffff;
  padding: 6px;
  object-fit: contain;
  border: 1px solid #e8e6f6;
  box-shadow: 0 4px 12px rgba(70, 70, 160, 0.10);
  flex-shrink: 0;
  /* 二维码是离散黑白块，禁用反走样——否则缩放时模块边缘会糊成黑块（v4.8.1） */
  image-rendering: pixelated;
  image-rendering: -moz-crisp-edges;
  image-rendering: crisp-edges;
}}
.qr-fallback {{
  width: 116px;
  height: 116px;
  border-radius: 12px;
  background: #ffffff;
  border: 1px dashed #cfcaf0;
  display: none;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  font-size: 11px;
  color: #8b8ba7;
  line-height: 1.6;
  text-align: center;
  flex-shrink: 0;
}}
.qr-hint {{
  font-size: 13px;
  color: #6d7386;
  line-height: 1.85;
  text-align: left;              /* v4.8.2 改回左对齐，紧贴 QR 图右侧 */
  letter-spacing: 0.2px;
  flex: 1;
  min-width: 0;
}}
.qr-hint b {{ color: #0aa957; font-weight: 700; font-size: 14.5px; letter-spacing: 0.4px; word-break: break-all; }}
/* ===== 备注：浅蓝底 + 左侧竖条 ===== */
.note-row {{
  display: flex;
  gap: 8px;
  margin-top: 10px;
  font-size: 13px;
  color: #4d5470;
  background: #f6f7ff;
  border-left: 3px solid #c9d2ff;
  border-radius: 10px;
  padding: 10px 14px;
  word-break: break-all;
}}
.note-row .note-label {{ color: #9aa1b8; flex-shrink: 0; }}
/* ===== 底部受理提示条 ===== */
.card-footer {{
  background: linear-gradient(120deg, #232a6b 0%, #3a3594 100%);
  color: #ffffff;
  padding: 15px 22px;
  display: flex;
  align-items: center;
  gap: 11px;
  font-size: 12.5px;
}}
.card-footer .dot {{
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #34d399;
  flex-shrink: 0;
  animation: pulse 1.2s ease-in-out infinite;
}}
@keyframes pulse {{
  0%, 100% {{ opacity: 1; }}
  50% {{ opacity: 0.3; }}
}}
.card-footer .reply {{ flex: 1; line-height: 1.6; opacity: 0.96; }}
.card-footer .reply b {{ color: #6ee7b7; font-weight: 700; }}
</style></head><body><div class="card">

<div class="card-header">
  <div class="icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg></div>
  <div class="title-group">
    <h2>新线索提醒{title_suffix}</h2>
    <div class="subtitle">{now_str}</div>
  </div>
</div>
"""

    for i, lead in enumerate(leads, 1):
        serial = serials[i - 1] if serials and i <= len(serials) else ""
        name = lead.get("name", "未知客户")
        avatar_data = lead.get("avatar", "")
        msg_time = lead.get("messageTime", lead.get("timestamp", ""))
        phone = lead.get("phone", "")
        wechat = lead.get("wechat", "")
        qrcode = lead.get("qrcode", "")
        note = lead.get("note", "")
        lead_type_raw = (lead.get("type") or "").strip()
        # 英文 type（lead/AD/biz...）→ 中文徽章文案；颜色类判定兼容中英文
        lead_type = _TYPE_CN.get(lead_type_raw.lower(), lead_type_raw)

        badge_class = "badge-unknown"
        badge_text = lead_type or "线索"
        # 注意判定顺序：先"经营/合作/企业"再"线索"——中文"经营线索"同时含
        # "经营"与"线索"子串，若"线索"在前会被误归为 lead（v4.8.0 修复）。
        if any(k in lead_type for k in ("经营", "合作", "企业")):
            badge_class = "badge-biz"
        elif "广告" in lead_type:
            badge_class = "badge-ad"
        elif any(k in lead_type for k in ("线索", "留资", "私信")):
            badge_class = "badge-lead"

        if avatar_data:
            avatar_html = f'<img src="{avatar_data}" alt="头像">'
        else:
            initial = name[0] if name else "?"
            avatar_html = f'<div class="avatar-placeholder">{initial}</div>'

        serial_html = f'<span class="lead-serial">{escape_html(serial)}</span>' if serial else ""

        # 联系方式区块：二维码优先展示，其次微信号大号；手机号始终高亮
        contact_rows = ""
        if qrcode:
            contact_rows += f"""    <div class="contact-row">
      <span class="contact-ico ico-qr"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg></span>
      <div class="qr-row">
        <div class="qr-tag"><span>二</span><span>维</span><span>码</span></div>
        <img class="qr-img" src="{qrcode}" alt="微信二维码"
             onerror="this.style.display='none';var f=this.nextElementSibling;if(f)f.style.display='flex';">
        <div class="qr-fallback">未获取到<br>二维码</div>
        <div class="qr-hint">
          <div>微信扫一扫</div>
          <div>直接添加客户</div>
          <div><b>{escape_html(wechat)}</b></div>
        </div>
      </div>
    </div>
"""
        if phone:
            contact_rows += f"""    <div class="contact-row">
      <span class="contact-ico ico-phone"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/></svg></span>
      <span class="contact-label">电话</span>
      <span class="contact-value phone">{escape_html(phone)}</span>
    </div>
"""
        if wechat:
            contact_rows += f"""    <div class="contact-row">
      <span class="contact-ico ico-wechat"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg></span>
      <span class="contact-label">微信</span>
      <span class="contact-value wechat">{escape_html(wechat)}</span>
    </div>
"""
        if not contact_rows:
            contact_rows = '    <div class="contact-row"><span class="contact-ico ico-info"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg></span><span class="contact-label">备注</span><span class="contact-value">无联系方式，见备注</span></div>\n'

        note_html = f"""  <div class="note-row"><span class="note-label">备注</span><span>{escape_html(note)}</span></div>
""" if note else ""

        html += f"""
<div class="lead-card">
  <div class="lead-top">
    <div class="avatar-wrap">{avatar_html}</div>
    <div class="lead-name-group">
      <div class="lead-name">{escape_html(name)}</div>
      <div class="lead-meta">
        {serial_html}
        <div class="lead-time">{escape_html(msg_time)}</div>
      </div>
    </div>
    <span class="lead-type-badge {badge_class}">{escape_html(badge_text)}</span>
  </div>
  <div class="contact-block">
{contact_rows}  </div>
{note_html}</div>
"""

    html += f"""
<div class="card-footer">
  <span class="dot"></span>
  <div class="reply">请在 <b>{reply_timeout}分钟</b> 内回复：<b>1</b>/好的 = 受理，<b>0</b>/没空 = 跳过</div>
</div>
</div></body></html>"""
    return html








async def _add_pending_lead(pending_id, lead_data, serial, group, member, engine, forwarded=False):
    timeout_minutes = _get_reply_timeout()
    async with _pending_lock:
        _pending_leads[pending_id] = {
            "lead": lead_data,
            "serial": serial,
            "group": group,
            "member": member,
            "engine": engine,
            "created_at": datetime.now(),
            "timeout_at": datetime.now() + timedelta(minutes=timeout_minutes),
            "status": "pending",
            "forwarded": forwarded
        }
    logger.info(f"[LeadsLinker] 跟踪待受理: {serial} -> @{member} (超时: {timeout_minutes}分钟)")


async def _check_timeouts():
    while True:
        await asyncio.sleep(30)
        now = datetime.now()
        async with _pending_lock:
            expired = [pid for pid, p in _pending_leads.items()
                       if p["status"] == "pending" and now >= p["timeout_at"]]

        for pid in expired:
            async with _pending_lock:
                pending = _pending_leads.pop(pid, None)

            if not pending:
                continue

            serial = pending["serial"]
            group = pending["group"]
            old_member = pending["member"]
            lead = pending["lead"]
            engine = pending["engine"]
            forwarded = pending.get("forwarded", False)

            # 超时自动转下一人开关：关闭时只记录"无人受理"，不再自动@下一位
            if not _auto_forward_enabled():
                logger.info(f"[LeadsLinker] 超时未受理且已关闭自动转发，跳过: {serial}")
                continue

            logger.info(f"[LeadsLinker] 超时未受理: {serial} (@{old_member}), 转发下一人")

            new_group, new_member = _select_target(skip_member=old_member)

            if not new_member or new_member == old_member:
                logger.info(f"[LeadsLinker] 无其他人可转发: {serial}")
                continue

            try:
                if forwarded:
                    short_msg = f"⬇️ {new_member} {serial} 请回复"
                    await engine.send_at_message(new_group, new_member, short_msg)
                else:
                    await _send_lead_batch(engine, [lead], [serial], new_group, new_member)

                await _add_pending_lead(pid, lead, serial, new_group, new_member, engine, forwarded=True)
            except Exception as e:
                logger.info(f"[LeadsLinker] 转发下一人失败: {e}")


async def _check_blocked():
    """Flush the blocked queue once the current time leaves the blocked window."""
    while True:
        await asyncio.sleep(30)
        try:
            config = _load_config()
            if _is_blocked_now(config):
                continue
            leftover, items = _drain_blocked(QUEUE_PATH)
            if not items:
                continue
            engine = globals().get("_current_engine")
            if engine is None:
                _enqueue_blocked(items)
                continue
            sent = 0
            for item in items:
                lead = item.get("lead")
                serial = item.get("serial")
                if not lead:
                    continue
                group_name, member_name = _select_target()
                if not group_name or not member_name:
                    _enqueue_blocked([item])
                    continue
                try:
                    await _forward_leads(engine, [lead], [serial], group_name, member_name)
                    if _reply_handling_enabled():
                        await _add_pending_lead(f"{serial}_{datetime.now().timestamp()}", lead, serial,
                                                group_name, member_name, engine)
                    sent += 1
                except Exception as e:
                    logger.info(f"[LeadsLinker] 阻塞队列转发失败: {e}")
            logger.info(f"[LeadsLinker] 阻塞队列已发送 {sent} 条")
        except Exception as e:
            logger.exception(f"[LeadsLinker] 阻塞队列刷新异常: {e}")


def _norm_group(name: str) -> str:
    """归一化群名，用于严格比对（与 MagpieBridge 的 target_guard 同口径）。

    NFKC（全角→半角，＆→&）→ 去群成员数后缀「客户群(12)」→ 去未读数后缀
    「3条新消息」→ 去尾部省略号 → 去所有空白 → 转小写。
    """
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", str(name))
    s = re.sub(r"[（(]\s*\d+\s*[)）]\s*$", "", s)
    s = re.sub(r"\d+\s*条新消息\s*$", "", s)
    s = re.sub(r"(\.{2,}|…+)+\s*$", "", s)
    s = re.sub(r"\s+", "", s)
    return s.strip().lower()


def _strip_trailing_digits(s: str) -> str:
    """去掉尾部编号：「客户群2」→「客户群」。"""
    return re.sub(r"\d+$", "", s)


def _group_matches(candidate: str, target: str) -> bool:
    """候选群名是否可以安全认定为 target 群。

    v3.0 起直接委托宿主 ``magpie.core.target_guard.name_matches``（ADR-0004：
    单一事实源，不再维护手抄副本，两处阈值从此不会漂移）。刻意**不使用子串
    匹配**——旧的 ``contact in p["group"]`` 是子串判断：「客户群」能命中
    「客户群2」的待受理记录（反之亦然），受理确认因此会发到错误的群。

    规则：精确 > OCR 截断前缀 > **同名编号群拒绝** > 相似度(≥0.85，长名)。
    宿主不可用时（理论上不会：插件只跑在宿主进程里）回退到本地同规则实现。
    """
    try:
        from magpie.core.target_guard import name_matches as _tg_name_matches
        return bool(_tg_name_matches(candidate, target, allow_truncation=True, reject_numbered=True))
    except Exception:
        pass
    c = _norm_group(candidate)
    t = _norm_group(target)
    if not c or not t:
        return False
    if c == t:
        return True
    # 同名 + 尾部编号（「客户群」vs「客户群2」）→ 一律拒绝
    if _strip_trailing_digits(c) == _strip_trailing_digits(t):
        return False
    # OCR 截断：候选是目标的前缀，且尾巴里不含数字、不超过 4 字
    if t.startswith(c) and len(c) >= 3 and len(c) >= len(t) * 0.6:
        tail = t[len(c):]
        if len(tail) <= 4 and not any(ch.isdigit() for ch in tail):
            return True
    # 方向性拒绝：目标是候选的前缀
    if c.startswith(t):
        return False
    # 短名不启用相似度
    if len(c) < 4 or len(t) < 4:
        return False
    return difflib.SequenceMatcher(None, c, t).ratio() >= 0.85


async def _handle_reply(msg, contact, engine):
    if not _reply_handling_enabled():
        return False
    msg_lower = msg.strip().lower()

    # 整词判定（拒收优先），避免 "不接" 被子串 "接" 误判为承接
    is_accept, is_reject = _classify_reply(msg_lower)

    if not is_accept and not is_reject:
        return False

    logger.info(f"[LeadsLinker] 检测到回复: msg={msg}, contact={contact}, pending_count={len(_pending_leads)}")

    async with _pending_lock:
        # 严格按群名匹配待受理线索（与 MagpieBridge core/target_guard 同口径）。
        #
        # 旧实现（本文件被移植前的 4.4.0 副本）有两个坑：
        #  ① `contact in p["group"]` 是子串判断 —— 「客户群」能命中「客户群2」
        #     的待受理记录，受理确认会发到错误的群；
        #  ② 匹配不到时**回退到全库所有 pending** —— A 群里的一句 "1" 会给
        #     B/C/D 群各发一条受理确认。这是"重复发送"最严重的一条根因。
        # 匹配不上就应当忽略本次回复，而不是扩大命中范围。
        matched_pends = [
            (pid, p) for pid, p in _pending_leads.items()
            if p["status"] == "pending" and _group_matches(p.get("group", ""), contact or "")
        ]

        if not matched_pends:
            logger.info(f"[LeadsLinker] 会话 {contact!r} 无对应的待受理线索，忽略本次回复")
            return False

        # 同一会话若积压多条 pending，只处理最近创建的那一条。
        # 旧实现对 matched_pends 做 for 循环发送，一人回复一次会向同一会话
        # 连发 N 条"已记录承接"。
        if len(matched_pends) > 1:
            matched_pends.sort(
                key=lambda kv: kv[1].get("created_at") or datetime.min,
                reverse=True,
            )
            skipped = [p.get("serial") for _, p in matched_pends[1:]]
            logger.warning(
                f"[LeadsLinker] 会话 {contact!r} 有 {len(matched_pends)} 条待受理，"
                f"本次只处理最新一条，其余忽略: {skipped}"
            )
            matched_pends = matched_pends[:1]

        if is_accept:
            for pid, pending in matched_pends:
                pending["status"] = "accepted"
                serial = pending["serial"]
                member = pending["member"]
                group = pending["group"]
                logger.info(f"[LeadsLinker] 受理确认: {serial} -> @{member}")

                try:
                    confirm_msg = f"✅ {member} 已记录承接 {serial}"
                    await engine.send_text(group, confirm_msg)
                except Exception as e:
                    logger.info(f"[LeadsLinker] 发送受理确认失败: {e}")

            for pid, _ in matched_pends:
                _pending_leads.pop(pid, None)
            return True

        elif is_reject:
            rejected = matched_pends[0]
            pid, pending = rejected
            _pending_leads.pop(pid, None)
            serial = pending["serial"]
            lead = pending["lead"]
            old_member = pending["member"]
            forwarded = pending.get("forwarded", False)
            logger.info(f"[LeadsLinker] 跳过: {serial} (@{old_member}), 转发下一人")

            new_group, new_member = _select_target(skip_member=old_member)

            if not new_member or new_member == old_member:
                logger.info(f"[LeadsLinker] 无其他人可转发: {serial}")
                return True

            try:
                if forwarded:
                    short_msg = f"⬇️ {new_member} {serial} 请回复"
                    await engine.send_at_message(new_group, new_member, short_msg)
                else:
                    await _send_lead_batch(engine, [lead], [serial], new_group, new_member)

                await _add_pending_lead(pid, lead, serial, new_group, new_member, engine, forwarded=True)
            except Exception as e:
                logger.info(f"[LeadsLinker] 转发下一人失败: {e}")

            return True

    return False


def _parse_transfer(cmd):
    """Parse: 转 L-202608-0001 @某人 or 转 L-202608-0001 某人"""
    import re
    pattern = r'转\s+(L-\d{6}-\d{4})\s+@?(\S+)'
    match = re.match(pattern, cmd)
    if match:
        return match.group(1), match.group(2)
    return None, None


async def _handle_transfer(cmd, sender, engine):
    serial, target_member = _parse_transfer(cmd)
    if not serial or not target_member:
        return "格式错误\n正确格式: /ll 转 L-202608-0001 @某人"

    async with _pending_lock:
        pending = None
        for pid, p in _pending_leads.items():
            if p["serial"] == serial and p["status"] == "pending":
                pending = (pid, p)
                break

        if not pending:
            return f"未找到序号 {serial} 的待受理线索"

        pid, p = pending
        old_member = p["member"]
        group = p["group"]
        lead = p["lead"]

        config = _load_config()
        members = config.get("members", [])
        is_admin = sender in members and sender == old_member

        if not is_admin and sender != old_member:
            return f"无权转单: 只有 @{old_member} 或管理员可以转此单"

        if target_member not in members:
            return f"@{target_member} 不在成员列表中"

        _pending_leads.pop(pid, None)

    try:
        await _send_lead_batch(engine, [lead], [serial], group, target_member)
        await _add_pending_lead(pid, lead, serial, group, target_member, engine)
    except Exception as e:
        logger.info(f"[LeadsLinker] 转单失败: {e}")
        return f"转单失败: {e}"

    return f"✅ {serial} 已从 @{old_member} 转给 @{target_member}"


async def action_send_leads(engine, **params):
    """OneBot action: send_leads - 接收浏览器插件转发的线索。

    v4.8.4 关键修复（重复转发风暴的根源）：
    旧版本在这里**同步 await 完整微信 UI 发送流水线**（搜索会话+渲染+图片+
    文字+@，一条 60~300 秒），而浏览器扩展的 fetch 超时只有 8 秒 → 扩展每次都
    判失败并按 5s×次数 退避重试 → 服务端每次重试都重新存一条新序号并**整批
    重发**，同一条线索从下午刷到晚上；UI 队列被重复任务堆满后真实发送反而级联
    超时。现在改为：在途去重 → 保存 → 后台任务发送 → **秒级返回已受理**。
    """
    leads_data = params.get("leads", [])

    if not leads_data:
        return {"ok": False, "error": "没有线索数据"}

    _ensure_timeout_task()
    globals()["_current_engine"] = engine

    # 在途去重：同一手机号/微信号已有后台发送任务 → 直接按"已受理"返回
    keys = list({k for lead in leads_data for k in _lead_dedup_keys(lead)})
    if await _acquire_inflight(keys):
        logger.info("[LeadsLinker] 重复请求(在途)，忽略: %s", keys)
        return {"ok": True, "saved": 0, "serials": [], "duplicate": True,
                "message": "同一线索正在发送中，本次请求已忽略，请勿重发"}
    try:
        saved_serials = []
        for lead in leads_data:
            try:
                serial, monthly, daily = _save_lead(lead)
                saved_serials.append(serial)
            except Exception as e:
                logger.info(f"[LeadsLinker] 保存线索失败: {e}")

        config = _load_config()

        # 阻塞时段:只暂存CSV,到非阻塞时段才依次发送
        if _is_blocked_now(config):
            items = [{"serial": saved_serials[i] if i < len(saved_serials) else f"q{i}", "lead": lead}
                     for i, lead in enumerate(leads_data)]
            _enqueue_blocked(items)
            return {"ok": True, "saved": len(saved_serials), "serials": saved_serials,
                    "queued": True, "message": "当前为阻塞时段，线索已暂存，将在非阻塞时段依次发送"}

        group_name, member_name = _select_target()

        if not group_name or not member_name:
            return {
                "ok": True,
                "saved": len(saved_serials),
                "serials": saved_serials,
                "queued": True,
                "message": "线索已保存，但当前无可用目标群/时段"
            }

        # 后台任务真正发送；本函数立即返回，扩展不再 8s 超时重试
        asyncio.create_task(_forward_task(engine, leads_data, saved_serials,
                                          group_name, member_name, keys))
        keys = []  # 所有权已移交 _forward_task（其 finally 负责释放）
        return {
            "ok": True,
            "saved": len(saved_serials),
            "serials": saved_serials,
            "queued": True,
            "accepted": True,
            "forwarded_to": group_name,
            "at_member": member_name,
            "reply_handling": _reply_handling_enabled(),
            "message": "已受理，后台串行发送中；发送完成前请勿重发同一线索"
        }
    finally:
        await _release_inflight(keys)


async def action_test_send_leads(engine, **params):
    """OneBot action: test_send_leads - 测试发送线索到微信群。

    测试线索同样会**保存进 CSV**并**跟踪受理**，这样测试时 @ 的人回复
    1/0/pass 也能正确记录/转单（与 send_leads 行为一致），方便联调。
    v4.8.4: 与 send_leads 一样改为秒回受理 + 后台任务（不写转发历史 CSV，
    与旧行为保持一致）。
    """
    leads_data = params.get("leads", [])

    if not leads_data:
        return {"ok": False, "error": "没有线索数据"}

    _ensure_timeout_task()
    globals()["_current_engine"] = engine

    keys = list({k for lead in leads_data for k in _lead_dedup_keys(lead)})
    if await _acquire_inflight(keys):
        return {"ok": True, "saved": 0, "serials": [], "duplicate": True,
                "message": "同一测试线索正在发送中，请勿连点"}
    try:
        # 1) save each lead to CSV (deduped by serial) and record the serial
        saved_serials = []
        for lead in leads_data:
            try:
                serial, monthly, daily = _save_lead(lead)
                saved_serials.append(serial)
            except Exception as e:
                logger.info(f"[LeadsLinker] 保存测试线索失败: {e}")

        group_name, member_name = _select_target()

        if not group_name or not member_name:
            config = _load_config()
            if _is_blocked_now(config):
                items = [{"serial": saved_serials[i] if i < len(saved_serials) else f"q{i}", "lead": lead}
                         for i, lead in enumerate(leads_data)]
                _enqueue_blocked(items)
            return {
                "ok": True,
                "saved": len(saved_serials),
                "serials": saved_serials,
                "message": "当前无可用目标群/时段，已保存"
            }

        asyncio.create_task(_forward_task(engine, leads_data, saved_serials,
                                          group_name, member_name, keys,
                                          record_history=False))
        keys = []
        return {
            "ok": True,
            "saved": len(saved_serials),
            "serials": saved_serials,
            "sent": len(leads_data),
            "accepted": True,
            "forwarded_to": group_name,
            "at_member": member_name,
            "reply_handling": _reply_handling_enabled(),
            "message": f"测试请求已受理，后台发送 {len(leads_data)} 条线索"
        }
    finally:
        await _release_inflight(keys)


async def action_get_config(engine, **params):
    """OneBot action: get_config - 读取当前转发配置"""
    return {"ok": True, "config": _load_config()}


async def action_update_config(engine, **params):
    """OneBot action: update_config - 更新转发配置

    支持两种来源：
    1. params 直接携带字段（groups/members/schedule/reply_timeout）
    2. params.patch: {key: value} 局部更新（后台插件设置格式）

    保存到本地 ~/LeadsLinker/config.json；若存在后台插件设置则同步更新
    PLUGIN_SETTINGS（MagpieBridge 控制台）。
    """
    patch = params.get("patch")
    if patch is not None:
        cfg = _load_config()
        groups = _split_lines(patch.get("groups", ""))
        if patch.get("groups") is not None:
            cfg["groups"] = [{"id": i + 1, "name": g} for i, g in enumerate(groups)]
        if patch.get("members") is not None:
            cfg["members"] = _split_lines(patch.get("members"))
        if patch.get("weekdayOrder") is not None:
            cfg.setdefault("schedule", {})["weekdayOrder"] = _split_lines(patch.get("weekdayOrder"))
        if patch.get("weekendOrder") is not None:
            cfg.setdefault("schedule", {})["weekendOrder"] = _split_lines(patch.get("weekendOrder"))
        if patch.get("reply_timeout") is not None:
            try:
                cfg["reply_timeout"] = int(patch["reply_timeout"])
            except (TypeError, ValueError):
                pass
        if patch.get("reply_handling") is not None:
            cfg["reply_handling"] = bool(patch.get("reply_handling"))
        if patch.get("blocked") is not None:
            cfg["blocked"] = _parse_blocked(patch.get("blocked"))
        if patch.get("slots") is not None:
            gpn, mem, sch = _build_groups({**(globals().get("PLUGIN_SETTINGS") or {}), "slots": patch.get("slots")})
            cfg["groups"] = gpn
            cfg["members"] = mem
            cfg["schedule"] = sch
    else:
        cfg = {
            "groups": params.get("groups", _load_config().get("groups", [])),
            "members": params.get("members", _load_config().get("members", [])),
            "schedule": params.get("schedule", _load_config().get("schedule", {})),
            "reply_timeout": params.get("reply_timeout", _load_config().get("reply_timeout", 5)),
            "reply_handling": params.get("reply_handling", _load_config().get("reply_handling", True)),
            "blocked": params.get("blocked", _load_config().get("blocked", [])),
        }

    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return {"ok": False, "error": f"保存配置失败: {e}"}

    # 同步后台插件设置（若存在 PLUGIN_SETTINGS）
    if globals().get("PLUGIN_SETTINGS") is not None:
        try:
            new_settings = dict(PLUGIN_SETTINGS)
            new_settings["groups"] = "\n".join(
                g.get("name", "") if isinstance(g, dict) else str(g)
                for g in cfg.get("groups", [])
            )
            new_settings["members"] = "\n".join(cfg.get("members", []))
            new_settings["weekdayOrder"] = "\n".join(cfg.get("schedule", {}).get("weekdayOrder", []))
            new_settings["weekendOrder"] = "\n".join(cfg.get("schedule", {}).get("weekendOrder", []))
            new_settings["reply_timeout"] = cfg.get("reply_timeout", 5)
            globals()["PLUGIN_SETTINGS"] = new_settings
            # 尝试通过引擎回写 MagpieBridge 插件设置存储
            pm = getattr(engine, "plugin_manager", None)
            if pm is not None:
                try:
                    pm.save_plugin_settings("leads_forwarder", new_settings)
                except Exception as e:
                    logger.info(f"[LeadsLinker] 同步后台设置失败: {e}")
        except Exception as e:
            logger.info(f"[LeadsLinker] 同步 PLUGIN_SETTINGS 失败: {e}")

    return {"ok": True, "config": cfg}


def _extract_command(msg):
    msg = msg.strip()
    for prefix in COMMAND_PREFIXES:
        if msg.lower().startswith(prefix + " ") or msg.lower() == prefix:
            return msg[len(prefix):].strip()
    return None


def _handle_query(msg, sender=None, engine=None):
    cmd = msg.strip()

    if cmd in ("帮助", "help", "菜单"):
        return _help_text()

    if cmd == "统计" or cmd == "线索统计" or cmd == "今日统计":
        return _today_stats()

    if cmd.startswith("月统计"):
        parts = cmd.split()
        month = parts[1] if len(parts) > 1 else None
        return _month_summary(month)

    if cmd.startswith("日统计"):
        parts = cmd.split()
        day = parts[1] if len(parts) > 1 else None
        return _day_summary(day)

    if cmd.startswith("查 "):
        keyword = cmd[2:].strip()
        return _do_query(keyword)

    if cmd.startswith("#") or cmd.upper().startswith("L-"):
        serial = cmd.lstrip("#").strip()
        return _query_by_serial_text(serial)

    if cmd == "待受理" or cmd == "pending":
        return _pending_list()

    if cmd.startswith("转 "):
        return ("TRANSFER:" + cmd)

    return f"未知命令: {cmd}\n输入 /ll 帮助 查看可用命令"


def _pending_list():
    if not _pending_leads:
        return "当前无待受理线索"

    lines = ["📋 待受理列表:"]
    lines.append("")
    for pid, p in _pending_leads.items():
        if p["status"] != "pending":
            continue
        serial = p["serial"]
        member = p["member"]
        lead = p["lead"]
        name = lead.get("name", "")
        timeout_at = p["timeout_at"].strftime("%H:%M")
        lines.append(f"  {serial} | {name} | @{member} | 超时: {timeout_at}")

    return "\n".join(lines)


def _help_text():
    return """📋 LeadsLinker 命令列表

格式: @MagpieBridge /ll 命令

查询:
  /ll 查 张三      按姓名(模糊)
  /ll 查 138       按手机号(模糊)
  /ll 查 abc       按微信号(模糊)
  /ll #L-202608-0001  按序号查询

统计:
  /ll 统计         今日+本月统计
  /ll 月统计       本月统计
  /ll 月统计 2026-08  指定月统计
  /ll 日统计       今日统计
  /ll 日统计 2026-08-30  指定日统计

受理:
  /ll 待受理       查看待受理列表
  /ll 转 序号 @某人  转单给他人

帮助:
  /ll 帮助         显示本帮助

注: 也可用 /leadslinker 替代 /ll"""


def _today_stats():
    now = datetime.now()
    month_str = now.strftime("%Y-%m")
    day_str = now.strftime("%Y-%m-%d")

    monthly_path = LEADS_DIR / f"leads_{month_str}.csv"
    daily_path = DAILY_DIR / f"leads_{day_str}.csv"

    month_count = len(_read_csv_all(monthly_path))
    day_count = len(_read_csv_all(daily_path))

    lines = [f"📊 线索统计"]
    lines.append(f"今日 ({day_str}): {day_count} 条")
    lines.append(f"本月 ({month_str}): {month_count} 条")

    if day_count > 0:
        rows = _read_csv_all(daily_path)
        lines.append("")
        lines.append("今日线索:")
        for row in rows[-10:]:
            serial = row.get("序号", "?")
            name = row.get("姓名", "")
            phone = row.get("电话", "")
            lines.append(f"  {serial} | {name} | {phone}")
        if day_count > 10:
            lines.append(f"  ... 共 {day_count} 条，仅显示最近10条")

    return "\n".join(lines)


def _do_query(keyword):
    results = []
    for csv_file in sorted(LEADS_DIR.glob("leads_????-??.csv"), reverse=True):
        rows = _read_csv_all(csv_file)
        for row in rows:
            name = row.get("姓名", "")
            phone = row.get("电话", "")
            wechat = row.get("微信号", "")
            if (keyword.lower() in name.lower() or
                keyword in phone or
                keyword.lower() in wechat.lower()):
                results.append(row)

    if not results:
        return f"未找到与 \"{keyword}\" 相关的线索"

    lines = [f"🔍 查询 \"{keyword}\" 共找到 {len(results)} 条:"]
    lines.append("")
    for row in results[:20]:
        lines.append(_format_lead_detail(row))
        lines.append("")
    if len(results) > 20:
        lines.append(f"... 共 {len(results)} 条，仅显示前20条")

    return "\n".join(lines)


def _query_by_serial_text(serial):
    if not serial.startswith("L-"):
        serial = f"L-{serial}"

    row = _query_by_serial(serial)

    if not row:
        return f"未找到序号为 {serial} 的线索"

    return f"🔍 线索详情:\n\n{_format_lead_detail(row)}"


async def on_message(ctx):
    msg = (ctx.get("message") or "").strip()
    contact = ctx.get("contact", "")
    engine = ctx.get("engine")
    sender = ctx.get("sender", {}).get("name", "") if isinstance(ctx.get("sender"), dict) else ""

    if not msg:
        return []

    logger.info("on_message: contact=%s, msg=%s, sender=%s", contact, msg[:50], sender)
    _ensure_timeout_task()
    if engine is not None:
        globals()["_current_engine"] = engine

    if engine:
        replied = await _handle_reply(msg, contact, engine)
        if replied:
            logger.info(f"[LeadsLinker] 回复已处理: {msg[:30]}")
            return []

    cmd = _extract_command(msg)

    if cmd is None:
        return []

    if cmd.startswith("转 "):
        if engine and sender:
            result = await _handle_transfer(cmd, sender, engine)
            return [{"type": "text", "data": {"text": result}}]
        return [{"type": "text", "data": {"text": "转单功能需要在群聊中使用"}}]

    result = _handle_query(cmd, sender=sender, engine=engine)
    if result:
        return [{"type": "text", "data": {"text": result}}]

    return []


# ================================================================
# LeadsLinker 调试通道 v4.0
# 让 AI/开发者通过本机 HTTP 获取浏览器插件页面的真实 DOM 信息。
#
# 架构:
#   AI ──POST /debug_query──▶ MagpieBridge(:3000, 插件中继)
#                                 │
#        ┌──轮询 debug_poll(1.5s)─┘
#   [浏览器扩展 background] ──▶ 抖音来客 content script 执行
#                                 │
#   AI ◀──debug_result── 扩展回传结果
#
# 全部 action 走既有 OneBot HTTP 通道(POST /{action})，仅本机 127.0.0.1 可达。
# 生产测试完建议在插件设置里关闭 debug_enabled。
# ================================================================


_DEBUG_RESULT_TTL = 600           # 结果保留 10 分钟
_DEBUG_INFLIGHT_TIMEOUT = 30      # 扩展执行超时(秒)，超时命令重新入队
_debug_pending: list = []         # 待执行命令队列 (FIFO)
_debug_inflight: dict = {}        # cmd_id -> {"cmd": ..., "claimed_at": float}
_debug_results: dict = {}         # cmd_id -> {result/error/ts}
_debug_heartbeat: dict = {}       # 扩展心跳: {url,title,version,ts,...}
_debug_lock = asyncio.Lock()
_debug_seq = 0


def _debug_enabled():
    settings = globals().get("PLUGIN_SETTINGS") or {}
    return bool(settings.get("debug_enabled", True))


def _debug_gen_id():
    global _debug_seq
    _debug_seq += 1
    return f"dbg_{int(time.time())}_{_debug_seq}"


def _debug_prune():
    """清理过期结果 / 超时未完成的飞行命令(重新入队)。调用方需持有锁。"""
    now = time.time()
    for k, v in list(_debug_results.items()):
        if now - v.get("ts", 0) > _DEBUG_RESULT_TTL:
            _debug_results.pop(k, None)
    for k, v in list(_debug_inflight.items()):
        if now - v.get("claimed_at", 0) > _DEBUG_INFLIGHT_TIMEOUT:
            _debug_inflight.pop(k, None)
            _debug_pending.append(v["cmd"])  # 重新入队等待再次领取


async def action_debug_status(engine, **params):
    """调试通道状态（AI/面板用）"""
    async with _debug_lock:
        _debug_prune()
        connected = bool(_debug_heartbeat) and (time.time() - _debug_heartbeat.get("ts", 0) < 10)
        return {
            "ok": True,
            "enabled": _debug_enabled(),
            "plugin_version": PLUGIN["version"],
            "extension_connected": connected,
            "extension": _debug_heartbeat,
            "pending": len(_debug_pending),
            "inflight": list(_debug_inflight.keys()),
            "results": len(_debug_results),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }


async def action_debug_submit(engine, **params):
    """提交一条调试命令给扩展执行（AI 用）
    params: {cmd, params?, priority?}  priority>0 插队
    """
    if not _debug_enabled():
        return {"ok": False, "error": "调试通道未开启（插件设置 debug_enabled=false）"}
    cmd_name = params.get("cmd")
    if not cmd_name:
        return {"ok": False, "error": "缺少 cmd"}
    cmd_id = _debug_gen_id()
    entry = {
        "cmd_id": cmd_id,
        "cmd": str(cmd_name),
        "params": params.get("params") or {},
        "created_at": time.time(),
        "priority": int(params.get("priority", 0)),
    }
    async with _debug_lock:
        _debug_prune()
        if entry["priority"] > 0:
            _debug_pending.insert(0, entry)
        else:
            _debug_pending.append(entry)
    logger.info("[LeadsLinker] 调试命令入队: %s (%s)", cmd_name, cmd_id)
    return {"ok": True, "cmd_id": cmd_id, "cmd": cmd_name}


async def action_debug_poll(engine, **params):
    """扩展轮询: 上报心跳 + 领取下一条命令（扩展用）"""
    global _debug_heartbeat
    hb = {
        "ts": time.time(),
        "url": params.get("tab_url", ""),
        "title": params.get("tab_title", ""),
        "tab_id": params.get("tab_id"),
        "extension_version": params.get("extension_version", ""),
        "time": datetime.now().strftime("%H:%M:%S"),
    }
    async with _debug_lock:
        _debug_prune()
        _debug_heartbeat = hb
        cmd = None
        while _debug_pending:
            cand = _debug_pending.pop(0)
            if cand["cmd_id"] in _debug_inflight:
                continue
            _debug_inflight[cand["cmd_id"]] = {"cmd": cand, "claimed_at": time.time()}
            cmd = cand
            break
    return {"ok": True, "heartbeat": hb, "command": cmd}


async def action_debug_result(engine, **params):
    """扩展上报执行结果（扩展用）"""
    cmd_id = params.get("cmd_id")
    result = params.get("result")
    error = params.get("error")
    async with _debug_lock:
        _debug_prune()
        _debug_inflight.pop(cmd_id, None)
        if cmd_id:
            _debug_results[cmd_id] = {
                "cmd_id": cmd_id,
                "result": result,
                "error": error,
                "ts": time.time(),
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
    logger.info("[LeadsLinker] 调试结果回传: %s", cmd_id)
    return {"ok": True, "cmd_id": cmd_id}


async def action_debug_fetch(engine, **params):
    """AI 拉取结果（AI 用）"""
    cmd_id = params.get("cmd_id")
    async with _debug_lock:
        _debug_prune()
        if cmd_id and cmd_id in _debug_results:
            r = _debug_results.pop(cmd_id, None)
            return {"ok": True, "status": "done", **r}
        if cmd_id and cmd_id in _debug_inflight:
            return {"ok": True, "status": "running", "cmd_id": cmd_id}
        return {"ok": True, "status": "pending", "cmd_id": cmd_id}


async def action_debug_query(engine, **params):
    """AI 一站式: 提交并等待结果（AI 用）
    params: {cmd, params?, timeout?}  timeout 默认 8 秒，最大 60
    """
    submit = await action_debug_submit(engine, **params)
    if not submit.get("ok"):
        return submit
    cmd_id = submit["cmd_id"]
    timeout = min(float(params.get("timeout", 8)), 60.0)
    deadline = time.time() + timeout
    while time.time() < deadline:
        await asyncio.sleep(0.5)
        r = await action_debug_fetch(engine, cmd_id=cmd_id)
        if r.get("status") == "done":
            return r
    return {
        "ok": True, "status": "timeout", "cmd_id": cmd_id,
        "hint": "可稍后用 POST /debug_fetch 带 cmd_id 取结果",
    }


async def action_debug_cancel(engine, **params):
    """取消未执行/执行中的命令（AI 用）"""
    cmd_id = params.get("cmd_id")
    async with _debug_lock:
        _debug_inflight.pop(cmd_id, None)
        global _debug_pending
        _debug_pending = [p for p in _debug_pending if p.get("cmd_id") != cmd_id]
    return {"ok": True, "cmd_id": cmd_id}


# ================================================================
# LeadsLinker 转发历史 CSV（去重）
# 文件: ~/LeadsLinker/leads_history.csv
# 作用: 转发成功时记录; 浏览器插件转发前先 leads_check 查重,
#       同手机号/微信号 在今天已转发 → 忽略(防止 7月留资9月看 / 同号重复 误发)
# ================================================================

HISTORY_CSV = Path(os.path.expanduser("~")) / "LeadsLinker" / "leads_history.csv"
_HISTORY_COLUMNS = ["forward_time", "msg_time", "account", "name", "phone", "wechat", "note", "type"]


def _history_read():
    if not HISTORY_CSV.exists():
        return []
    with open(HISTORY_CSV, "r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _history_write_row(row):
    HISTORY_CSV.parent.mkdir(parents=True, exist_ok=True)
    is_new = not HISTORY_CSV.exists()
    with open(HISTORY_CSV, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=_HISTORY_COLUMNS)
        if is_new:
            w.writeheader()
        w.writerow(row)


def _history_record(lead):
    """转发成功后记录一条线索到历史 CSV。"""
    row = {
        "forward_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "msg_time": str(lead.get("messageTime") or lead.get("msg_time") or ""),
        "account": str(lead.get("account") or ""),
        "name": str(lead.get("name") or ""),
        "phone": str(lead.get("phone") or ""),
        "wechat": str(lead.get("wechat") or ""),
        "note": str(lead.get("note") or "")[:200],
        "type": str(lead.get("type") or ""),
    }
    _history_write_row(row)


async def action_leads_check(engine, **params):
    """浏览器插件转发前查重: 同手机号/微信号今天是否已处理(转发/收到过)。
    params: {phone?, wechat?}
    数据源双保险:
      1) leads_history.csv      (v4.1+ 转发成功记录)
      2) leads/leads_YYYY-MM.csv (v3+ 收到线索记录, 覆盖 history 缺失的历史)
    返回: {ok, duplicate, reason, last}
    """
    phone = str(params.get("phone") or "")
    wechat = str(params.get("wechat") or "")
    if not phone and not wechat:
        return {"ok": True, "duplicate": False, "reason": "no_contact"}
    today = datetime.now().strftime("%Y-%m-%d")
    month = datetime.now().strftime("%Y-%m")

    def _is_today(row):
        mt = (row.get("msg_time") or "").strip()[:10]
        ft = (row.get("forward_time") or "").strip()[:10]
        return mt == today or (not mt and ft == today)

    # 数据源1: 转发历史 CSV
    try:
        records = _history_read()
    except Exception:
        records = []
    for r in reversed(records):
        if ((phone and r.get("phone") == phone) or (wechat and r.get("wechat") == wechat)) and _is_today(r):
            return {"ok": True, "duplicate": True, "reason": "forwarded_today",
                    "last": r, "last_msg_time": r.get("msg_time"), "last_forward_time": r.get("forward_time")}

    # 数据源2: 收到的线索 CSV (leads_YYYY-MM.csv), 同联系方式今天收到过 → 视为已处理
    try:
        monthly = LEADS_DIR / f"leads_{month}.csv"
        if monthly.exists():
            with open(monthly, "r", encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    rp = (r.get("电话") or "").strip()
                    rw = (r.get("微信号") or "").strip()
                    if not ((phone and rp == phone) or (wechat and rw == wechat)):
                        continue
                    # 判定"今天收到": 优先"时间"列(UTC ISO)转本地; 其次"消息时间"HH:MM=今天
                    recv = (r.get("时间") or "").strip()
                    is_today = False
                    try:
                        if recv:
                            dt = datetime.fromisoformat(recv.replace("Z", "+00:00"))
                            dt_local = dt + timedelta(hours=8)
                            is_today = dt_local.strftime("%Y-%m-%d") == today
                    except Exception:
                        is_today = False
                    if not is_today:
                        mt = (r.get("消息时间") or "").strip()
                        if re.match(r"^\d{1,2}:\d{2}$", mt):
                            is_today = True
                    if is_today:
                        return {"ok": True, "duplicate": True, "reason": "received_today",
                                "last": r, "last_msg_time": recv or (r.get("消息时间") or "")}
    except Exception as e:
        logger.warning(f"[LeadsLinker] 读取线索CSV查重失败: {e}")

    return {"ok": True, "duplicate": False, "last": None}


async def action_blocked_status(engine, **params):
    """浏览器扩展「线索列表」页查询服务端积压/阻塞状态。
    返回: {ok, queued, blocked}
    """
    try:
        q = json.loads(QUEUE_PATH.read_text(encoding="utf-8")) if QUEUE_PATH.exists() else []
    except Exception:
        q = []
    config = _load_config()
    return {"ok": True, "queued": len(q), "blocked": _is_blocked_now(config)}


async def action_flush_blocked(engine, **params):
    """浏览器扩展「立即重试」：立即冲刷服务端积压(阻塞)队列。

    与 send_leads 同款：秒级返回"已受理"，实际发送交给后台任务，避免
    扩展 15s 超时。返回: {ok, blocked, queued, message}
    """
    globals()["_current_engine"] = engine
    config = _load_config()
    blocked = _is_blocked_now(config)
    try:
        q = json.loads(QUEUE_PATH.read_text(encoding="utf-8")) if QUEUE_PATH.exists() else []
    except Exception:
        q = []
    if q:
        asyncio.create_task(_flush_blocked_queue_now())
        return {"ok": True, "blocked": blocked, "queued": len(q),
                "message": "已开始冲刷积压线索，发送中（不要重复点击）"}
    return {"ok": True, "blocked": blocked, "queued": 0, "message": "无积压线索"}


async def _flush_blocked_queue_now():
    """一次性冲刷阻塞队列（供 flush_blocked 后台调用；与 _check_blocked 同逻辑）。"""
    try:
        leftover, items = _drain_blocked(QUEUE_PATH)
        if not items:
            return
        engine = globals().get("_current_engine")
        if engine is None:
            _enqueue_blocked(items)
            return
        sent = 0
        for item in items:
            lead = item.get("lead")
            serial = item.get("serial")
            if not lead:
                continue
            group_name, member_name = _select_target()
            if not group_name or not member_name:
                _enqueue_blocked([item])
                continue
            try:
                await _forward_leads(engine, [lead], [serial], group_name, member_name)
                if _reply_handling_enabled():
                    await _add_pending_lead(f"{serial}_{datetime.now().timestamp()}", lead, serial,
                                            group_name, member_name, engine)
                sent += 1
            except Exception as e:
                logger.info(f"[LeadsLinker] 手动冲刷阻塞队列失败: {e}")
                _enqueue_blocked([item])
        logger.info(f"[LeadsLinker] 手动冲刷阻塞队列已发送 {sent} 条")
    except Exception as e:
        logger.exception(f"[LeadsLinker] 手动冲刷阻塞队列异常: {e}")
