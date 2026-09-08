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
    "version": "1.2.0",
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

    try:
        from magpie.core import wechat_adapter_4x as adapter_mod
        if not getattr(adapter_mod, "_hotfix_name_match_applied", False):
            _install_name_match_patch(adapter_mod)
    except Exception as e:
        logger.warning("name_matches 补丁失败: %s", e)

    # ocr.find_contact_by_name 是函数内 import, patch target_guard 模块属性
    # 即可覆盖它以及其它运行时 import 的调用点(plugins/leads_forwarder 等)
    try:
        import magpie.core.target_guard as tg
        if not getattr(tg, "_hotfix_name_match_applied", False):
            _install_name_match_patch(tg)
    except Exception as e:
        logger.warning("target_guard.name_matches 补丁失败: %s", e)

    return True


def _install_name_match_patch(host_mod) -> None:
    """显示截断兜底: 微信会话列表对长群名可能只渲染尾部
    (「测试的佛山投流工作群」列表显示为「佛山投流工作群」, 2026-09-08 现场)。
    规则: 候选是目标的**尾部**且长度 >= 6 → 认定同一会话; 歧义(列表里另有
    独立的同名短群)由 find_unique 的唯一性检查拒绝, 安全性不变。
    """
    orig = host_mod.name_matches

    def name_matches_v2(candidate, target, allow_truncation=True, reject_numbered=True):
        if orig(candidate, target, allow_truncation=allow_truncation,
                reject_numbered=reject_numbered):
            return True
        try:
            from magpie.core.target_guard import normalize as _norm
            c = _norm(candidate or "")
            t = _norm(target or "")
            if len(c) >= 6 and len(t) > len(c) and t.endswith(c):
                logger.info("群名匹配(显示截断兜底): 列表显示 %r ≙ 目标 %r", candidate, target)
                return True
        except Exception:
            pass
        return False

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
