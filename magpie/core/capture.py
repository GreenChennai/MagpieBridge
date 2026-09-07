"""GDI 截图 —— 全项目唯一的屏幕/窗口抓图实现（ADR-0005 去重）。

历史上同一段 BitBlt/PrintWindow + BITMAPINFOHEADER 代码在
wechat_adapter_4x / message_monitor / admin command_handler 里各抄了一份，
前两份还是"用完不检查返回值"的旧写法。v5.0 起只保留这里一份：

- :func:`grab_screen_region` 屏幕 BitBlt —— 不进入目标进程、天然包含弹层；
- :func:`grab_printwindow`   PrintWindow 兜底 —— 窗口被遮挡时用；
- :func:`is_window_occluded` 判断窗口中心点是否被别的窗口盖住（选路用）。
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from typing import Optional

from PIL import Image

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def _dib_to_image(hdc, bmp, w: int, h: int) -> Optional[Image.Image]:
    bmpinfo = BITMAPINFOHEADER()
    bmpinfo.biSize = ctypes.sizeof(bmpinfo)
    bmpinfo.biWidth = w
    bmpinfo.biHeight = -h  # top-down
    bmpinfo.biPlanes = 1
    bmpinfo.biBitCount = 32
    bmpinfo.biCompression = 0
    bmp_data = ctypes.create_string_buffer(w * h * 4)
    if not gdi32.GetDIBits(hdc, bmp, 0, h, bmp_data, ctypes.byref(bmpinfo), 0):
        return None
    img = Image.frombuffer("RGBX", (w, h), bmp_data, "raw", "BGRX", 0, 1)
    return img.convert("RGB")


def grab_screen_region(x: int, y: int, w: int, h: int) -> Optional[Image.Image]:
    """BitBlt 屏幕指定矩形 —— 不经过微信进程，且能带上微信自己的弹层窗口。"""
    sdc = mfc = bmp = None
    try:
        sdc = user32.GetDC(None)
        mfc = gdi32.CreateCompatibleDC(sdc)
        bmp = gdi32.CreateCompatibleBitmap(sdc, w, h)
        gdi32.SelectObject(mfc, bmp)
        if not gdi32.BitBlt(mfc, 0, 0, w, h, sdc, x, y, 0x00CC0020):
            return None
        img = _dib_to_image(mfc, bmp, w, h)
        return img
    except Exception:
        logger.exception("screen BitBlt capture failed")
        return None
    finally:
        if bmp:
            gdi32.DeleteObject(bmp)
        if mfc:
            gdi32.DeleteDC(mfc)
        if sdc:
            user32.ReleaseDC(None, sdc)


def grab_printwindow(hwnd: int, w: int, h: int) -> Optional[Image.Image]:
    """PrintWindow 兜底（窗口被其他程序遮挡时才用）。"""
    hdc = mfc = bmp = None
    try:
        hdc = user32.GetDC(hwnd)
        mfc = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        gdi32.SelectObject(mfc, bmp)
        user32.PrintWindow(hwnd, mfc, 3)
        return _dib_to_image(mfc, bmp, w, h)
    except Exception:
        logger.exception("Failed to capture screenshot")
        return None
    finally:
        if bmp:
            gdi32.DeleteObject(bmp)
        if mfc:
            gdi32.DeleteDC(mfc)
        if hdc:
            user32.ReleaseDC(hwnd, hdc)


def is_window_occluded(hwnd: int, x: int, y: int, w: int, h: int) -> bool:
    """True 当窗口中心点被其它顶层窗口占据（微信的弹层不算——它们不是根属主）。"""
    cx, cy = x + w // 2, y + h // 2
    top = user32.WindowFromPoint(wintypes.POINT(cx, cy))
    root = user32.GetAncestor(top, 3) if top else 0  # GA_ROOTOWNER
    return not (top == hwnd or (root and root == hwnd))
