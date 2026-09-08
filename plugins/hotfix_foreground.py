# -*- coding: utf-8 -*-
"""[热修补丁] 置前备选链 + 前台就绪等待(ADR-0006 现场补丁)。

背景: 运行中的 MagpieBridge 实例是已锁定的旧 exe, tscon 挂回控制台后的
「无前台窗口期」内 SetForegroundWindow 全部被拒 → 发送三步全 failed
(L-202609-0217/0218 事故)。正式修复已进新 exe(update/MagpieBridge.exe),
本插件通过 monkey-patch 让**旧实例不重启**立即获得等价能力:

- WindowManager._force_foreground: AttachThreadInput 失败后依次尝试
  SwitchToThisWindow、最小化+恢复(实验矩阵 5/5 有效);
- WindowManager.restore_and_focus: 固定 3 次重试 → 45s 截止循环等桌面就绪。

幂等: 重复 reload 不会二次包装。插件升级到含正式修复的 exe 后, 本插件
自动退化为 no-op(检测到新实现自带 SwitchToThisWindow 时跳过)。
"""

PLUGIN = {
    "name": "hotfix_foreground",
    "version": "1.4.1",
    "description": "热修补丁: 置前备选链+前台就绪等待(ADR-0006), 供旧实例热加载",
    "author": "MagpieBridge",
    "events": [],
    "actions": [],
}

import logging
import time as _time

logger = logging.getLogger("hotfix_foreground")


def _install() -> bool:
    try:
        from magpie.core import window_manager as wm
    except Exception as e:
        logger.warning("无法导入 window_manager: %s", e)
        return False

    if not getattr(wm, "_hotfix_foreground_applied", False):
        _install_foreground_patch(wm)

    # v1.4.2: 撤销 v1.2 装的「尾部截断兜底」wrapper(现场证明危险——列表中
    # 存在真实同尾短群「佛山投流工作群」≠「测试的佛山投流工作群」), 从影子
    # 模块恢复纯净实现。无条件执行, 不吃 applied 标记。
    try:
        import importlib.util as _ilu
        from magpie.core import target_guard as tg

        spec = _ilu.spec_from_file_location("_tg_clean_v142", tg.__file__)
        clean = _ilu.module_from_spec(spec)
        spec.loader.exec_module(clean)
        tg.name_matches = clean.name_matches
        from magpie.core import wechat_adapter_4x as adapter_mod
        adapter_mod.name_matches = clean.name_matches
        logger.info("name_matches 已恢复纯净实现(尾部截断兜底撤销)")
    except Exception as e:
        logger.warning("name_matches 恢复失败: %s", e)

    try:
        from magpie.core import wechat_adapter_4x as adapter_mod
        if getattr(adapter_mod.WeChat4xAdapter, "_hotfix_title_match_applied", False):
            _restore_title_match(adapter_mod)
    except Exception as e:
        logger.warning("_title_matches 恢复失败: %s", e)

    try:
        from magpie.core import wechat_adapter_4x as adapter_mod
        if not getattr(adapter_mod.WeChat4xAdapter, "_hotfix_search_applied", False):
            _install_search_expand_patch(adapter_mod)
    except Exception as e:
        logger.warning("search_contact 补丁失败: %s", e)

    try:
        from magpie.core import wechat_adapter_4x as adapter_mod
        if not getattr(adapter_mod.WeChat4xAdapter, "_hotfix_hasname_applied", False):
            _install_hasname_patch(adapter_mod)
    except Exception as e:
        logger.warning("_input_box_has_name 补丁失败: %s", e)

    return True


def _install_hasname_patch(adapter_mod) -> None:
    """@ 选择后验证归一化(ADR-0008): 微信插入的是显示名"Green Chennai",
    配置名是"Green_Chennai" —— 下划线/空格/大小写必须归一再比, 否则唯一
    候选 Enter 选中后验证永远失败, 走不必要的粘贴降级。"""
    import re as _re

    def _norm_id(s):
        return _re.sub(r"[\s_@＠….·]+", "", s or "").lower()

    def has_name_v2(self, name):
        try:
            shot = self._get_screenshot()
            pos = self.get_input_box_position()
            if not shot or not pos:
                return False
            x, y = pos[0], pos[1]
            region = shot.crop((max(0, x - 250), max(0, y - 80),
                                min(shot.width, x + 300), min(shot.height, y + 25)))
            from magpie.core.ocr import ocr_recognize
            joined = "".join(t.get("text", "") for t in ocr_recognize(region))
            n = _norm_id(name)
            j = _norm_id(joined)
            if not n:
                return False
            if n in j:
                return True
            return bool(n[-3:]) and n[-3:] in j
        except Exception:
            return False

    adapter_mod.WeChat4xAdapter._input_box_has_name = has_name_v2
    adapter_mod.WeChat4xAdapter._hotfix_hasname_applied = True
    logger.info("_input_box_has_name 归一化验证已安装(WeChat4xAdapter)")


def _restore_title_match(adapter_mod) -> None:
    """从 v1.3 wrapper 的闭包里恢复原始 _title_matches 函数。"""
    import types

    cur = getattr(adapter_mod.WeChat4xAdapter, "_title_matches")
    fn = getattr(cur, "__func__", cur)
    for cell in (getattr(fn, "__closure__", None) or []):
        try:
            inner = cell.cell_contents
        except (ValueError, TypeError):
            continue
        f = getattr(inner, "__func__", inner)
        if isinstance(f, types.FunctionType) and f.__name__ == "_title_matches":
            adapter_mod.WeChat4xAdapter._title_matches = f
            logger.info("_title_matches 已恢复原始实现(子串兜底撤销)")
            return
    logger.warning("未能从闭包恢复 _title_matches(保持现状)")


def _install_search_expand_patch(adapter_mod) -> None:
    """折叠置顶聊天展开重扫(ADR-0007 现场最终修复)。

    现场真相: 目标群「测试的佛山投流工作群」在**折叠的置顶聊天区**里,
    主会话列表不可见; 列表中的「佛山投流工作群」是另一个真实群。
    search_contact 扫不到目标时 → OCR 找「折叠置顶聊天」按钮 → 点击展开
    → 再扫一遍。
    """
    import time as _t

    orig_search = adapter_mod.WeChat4xAdapter.search_contact

    def _expand_folded_top_chats(self) -> bool:
        try:
            from magpie.core import capture, ocr
            r = self._window.get_window_rect()
            if not r:
                return False
            img = capture.grab_screen_region(r[0], r[1], r[2], r[3])
            if not img:
                return False
            for t in ocr.ocr_recognize(img):
                if "折叠置顶聊天" in (t["text"] or ""):
                    xs = [p[0] for p in t["bbox"]]
                    ys = [p[1] for p in t["bbox"]]
                    cx = r[0] + int(sum(xs) / len(xs))
                    cy = r[1] + int(sum(ys) / len(ys))
                    logger.info("发现折叠置顶聊天按钮(%d,%d), 点击展开", cx, cy)
                    self._human.click_at(cx, cy)
                    _t.sleep(1.2)
                    return True
        except Exception:
            logger.exception("展开折叠置顶聊天异常")
        return False

    def search_contact_v2(self, name, *args, **kwargs):
        ok = orig_search(self, name, *args, **kwargs)
        if ok:
            return True
        logger.info("主列表未找到 %r, 尝试展开折叠置顶聊天后重扫一次", name)
        if _expand_folded_top_chats(self):
            return orig_search(self, name, *args, **kwargs)
        return False

    adapter_mod.WeChat4xAdapter.search_contact = search_contact_v2
    adapter_mod.WeChat4xAdapter._hotfix_search_applied = True
    logger.info("search_contact 折叠置顶展开重扫已安装")


def _install_title_match_patch(adapter_mod) -> None:
    """标题验证子串兜底(ADR-0007): 切换会话后 OCR 读聊天区标题复核, 现场
    发现标题被稳定截断+噪声 —— 「测试的佛山投流工作群(13)」读成
    「州投流工作群(13)0」。标题验证是**单标题、无候选歧义**场景, 子串规则安全:
    净化(去成员数/尾部数字)后是目标的子串且长度>=6 → 认定已切换到目标。
    """
    import re as _re

    orig = adapter_mod.WeChat4xAdapter._title_matches

    def title_matches_v2(self, title, target):
        # v1.4 撤销子串兜底: "佛山投流工作群(13)" 是另一个真实群,
        # 放行会把消息发进错误的群! 标题校验保持严格。
        return orig(self, title, target)

    adapter_mod.WeChat4xAdapter._title_matches = title_matches_v2
    adapter_mod.WeChat4xAdapter._hotfix_title_match_applied = True
    logger.info("_title_matches 子串兜底已安装(WeChat4xAdapter)")


def _install_name_match_patch(host_mod) -> None:
    """显示截断兜底: 微信会话列表对长群名可能只渲染尾部
    (「测试的佛山投流工作群」列表显示为「佛山投流工作群」, 2026-09-08 现场)。
    规则: 候选是目标的**尾部**且长度 >= 6 → 认定同一会话; 歧义(列表里另有
    独立的同名短群)由 find_unique 的唯一性检查拒绝, 安全性不变。
    """
    orig = host_mod.name_matches

    def name_matches_v2(candidate, target, allow_truncation=True, reject_numbered=True):
        # v1.4 撤销尾部截断兜底: 现场证明列表中存在真实同尾短群
        # ("佛山投流工作群" ≠ "测试的佛山投流工作群"), 放行会点错群!
        return orig(candidate, target, allow_truncation=allow_truncation,
                    reject_numbered=reject_numbered)

    host_mod.name_matches = name_matches_v2
    host_mod._hotfix_name_match_applied = True
    logger.info("name_matches 显示截断兜底已安装: %s", host_mod.__name__)


def _install_foreground_patch(wm) -> None:

    user32 = wm.user32
    orig_force = wm.WindowManager._force_foreground
    orig_restore = wm.WindowManager.restore_and_focus

    def _fg_ok(hwnd):
        return bool(hwnd) and user32.GetForegroundWindow() == hwnd

    def force_foreground_v2(self, hwnd):
        ok = orig_force(self, hwnd)
        if _fg_ok(hwnd):
            return True
        # 备选 1: SwitchToThisWindow(强制切换, 前台锁豁免)
        try:
            user32.SwitchToThisWindow(hwnd, True)
            _time.sleep(0.3)
            if _fg_ok(hwnd):
                logger.info("置前(热修): SwitchToThisWindow 成功 hwnd=%s", hwnd)
                return True
        except Exception:
            pass
        # 备选 2: 最小化→恢复 强制激活(位置不变)
        try:
            user32.ShowWindow(hwnd, 6)   # SW_MINIMIZE
            _time.sleep(0.25)
            user32.ShowWindow(hwnd, 9)   # SW_RESTORE
            _time.sleep(0.3)
            if _fg_ok(hwnd):
                logger.info("置前(热修): 最小化+恢复 成功 hwnd=%s", hwnd)
                return True
        except Exception:
            pass
        return bool(ok)

    def restore_and_focus_v2(self):
        hwnd = self._hwnd or self.find_window()
        if not hwnd:
            return False
        try:
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)   # SW_RESTORE
            elif not user32.IsWindowVisible(hwnd):
                user32.ShowWindow(hwnd, 9)
                user32.ShowWindow(hwnd, 5)   # SW_SHOW
            _time.sleep(0.25)
            deadline = _time.time() + 45.0   # 前台就绪等待预算(ADR-0006)
            attempt = 0
            got = False
            while _time.time() < deadline:
                attempt += 1
                if force_foreground_v2(self, hwnd):
                    got = True
                    break
                user32.keybd_event(0x12, 0, 0, 0)   # Alt down
                user32.keybd_event(0x12, 0, 2, 0)   # Alt up
                logger.info("置前(热修)第 %d 次失败, %.0fs 内重试等桌面就绪",
                            attempt, deadline - _time.time())
                _time.sleep(3)
            if not got:
                got = self._click_titlebar_to_focus(hwnd)
            _time.sleep(0.15)
            visible = bool(user32.IsWindowVisible(hwnd))
            ok = visible and _fg_ok(hwnd)
            if ok:
                self._hwnd = hwnd
            else:
                logger.warning("置前(热修)仍未成功: visible=%s fg=%s hwnd=%s",
                               visible, user32.GetForegroundWindow(), hwnd)
            return ok
        except Exception:
            logger.exception("restore_and_focus(热修) 异常")
            return False

    wm.WindowManager._force_foreground = force_foreground_v2
    wm.WindowManager.restore_and_focus = restore_and_focus_v2
    wm._hotfix_foreground_applied = True
    logger.info("置前热补丁已安装(备选链+45s 前台就绪等待)")
    return True


_install()
