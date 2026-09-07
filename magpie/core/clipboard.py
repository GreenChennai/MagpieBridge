"""Clipboard operations for text and image handling."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import io
import logging
import subprocess
import time
from typing import Optional

from PIL import Image

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Win32 handle/pointer-returning APIs must declare 64-bit restype, otherwise
# ctypes truncates the 64-bit handle to 32 bits (GlobalAlloc returns garbage).
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalLock.restype = ctypes.c_void_p
user32.SetClipboardData.restype = ctypes.c_void_p
user32.GetClipboardData.restype = ctypes.c_void_p
user32.OpenClipboard.restype = ctypes.c_int
user32.EmptyClipboard.restype = ctypes.c_int

# ... and explicit argtypes so c_void_p handles pass through unchanged.
kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
user32.GetClipboardData.argtypes = [ctypes.c_uint]
user32.OpenClipboard.argtypes = [ctypes.c_void_p]


class Clipboard:
    """Windows clipboard operations for text and images."""

    # winuser.h constants
    CF_UNICODETEXT = 13
    CF_DIB = 8
    GMEM_MOVEABLE = 0x0002
    GMEM_ZEROINIT = 0x0040

    def set_text(self, text: str, retries: int = 3) -> bool:
        """Copy text to clipboard using Win32 API only (no PowerShell).

        PowerShell Set-Clipboard is unreliable - use ctypes directly.
        """
        for attempt in range(retries):
            if self._set_text_ctypes(text):
                return True
            logger.warning("Clipboard text write failed (attempt %d/%d)", attempt + 1, retries)
            time.sleep(0.5 + attempt * 0.4)
        logger.error("Failed to set clipboard text after %d retries", retries)
        return False

    def _set_text_ctypes(self, text: str) -> bool:
        """Set clipboard text via the Win32 API (CF_UNICODETEXT).

        Returns False when the clipboard is locked by another process
        (OpenClipboard fails) or allocation fails - caller retries.
        NOTE: after SetClipboardData succeeds the system OWNS the handle
        (do NOT GlobalFree it - that would orphan the clipboard data).
        """
        try:
            if not user32.OpenClipboard(None):
                return False
            try:
                user32.EmptyClipboard()
                data = (text + "\x00").encode("utf-16-le")
                h_mem = kernel32.GlobalAlloc(
                    self.GMEM_MOVEABLE | self.GMEM_ZEROINIT, len(data)
                )
                if not h_mem:
                    return False
                ptr = kernel32.GlobalLock(h_mem)
                if not ptr:
                    kernel32.GlobalFree(h_mem)
                    return False
                try:
                    ctypes.memmove(ptr, data, len(data))
                finally:
                    kernel32.GlobalUnlock(h_mem)
                if not user32.SetClipboardData(self.CF_UNICODETEXT, h_mem):
                    # ownership stays with us on failure - free it
                    kernel32.GlobalFree(h_mem)
                    return False
                logger.info("Text copied to clipboard via Win32 API (%d chars)", len(text))
                return True
            finally:
                user32.CloseClipboard()
        except Exception:
            logger.exception("ctypes set clipboard failed")
            return False

    def set_image(self, image: Image.Image, retries: int = 3) -> bool:
        """Copy PIL Image to clipboard using Win32 CF_DIB (no PowerShell).

        PowerShell's Set-Clipboard is unreliable for images - use ctypes only.
        """
        for attempt in range(retries):
            if self._set_image_ctypes(image):
                return True
            logger.warning("Image clipboard write failed (attempt %d/%d)", attempt + 1, retries)
            time.sleep(0.5 + attempt * 0.4)
        logger.error("Failed to set clipboard image after %d retries", retries)
        return False

    @staticmethod
    def _image_to_dib(image: Image.Image) -> bytes:
        """PIL Image -> DIB bytes (BITMAPINFOHEADER + pixels, no file header)."""
        buf = io.BytesIO()
        image.save(buf, 'BMP')
        bmp = buf.getvalue()
        return bmp[14:]  # strip BITMAPFILEHEADER, keep DIB

    def _set_image_ctypes(self, image: Image.Image) -> bool:
        """Set image clipboard via Win32 CF_DIB (no PowerShell dependency)."""
        try:
            dib = self._image_to_dib(image)
            if not user32.OpenClipboard(None):
                return False
            try:
                user32.EmptyClipboard()
                h_mem = kernel32.GlobalAlloc(
                    self.GMEM_MOVEABLE | self.GMEM_ZEROINIT, len(dib)
                )
                if not h_mem:
                    return False
                ptr = kernel32.GlobalLock(h_mem)
                if not ptr:
                    kernel32.GlobalFree(h_mem)
                    return False
                try:
                    ctypes.memmove(ptr, dib, len(dib))
                finally:
                    kernel32.GlobalUnlock(h_mem)
                if not user32.SetClipboardData(self.CF_DIB, h_mem):
                    kernel32.GlobalFree(h_mem)
                    return False
                logger.info("Image copied to clipboard via Win32 CF_DIB (%d bytes)", len(dib))
                return True
            finally:
                user32.CloseClipboard()
        except Exception:
            logger.exception("ctypes set clipboard image failed")
            return False

    def set_image_from_file(self, file_path: str) -> bool:
        """Load image from file and copy to clipboard (PS first, ctypes fallback)."""
        try:
            from PIL import Image as PILImage
            image = PILImage.open(file_path)
            image.load()
            return self.set_image(image)
        except Exception:
            logger.exception("Failed to load image from file")
            return False

    def paste_text(self, text: str) -> bool:
        """Copy text to clipboard and simulate Ctrl+V."""
        if not self.set_text(text):
            return False
        time.sleep(0.1)
        self._send_ctrl_v()
        return True

    def paste_image(self, image: Image.Image) -> bool:
        """Copy image to clipboard and simulate Ctrl+V."""
        if not self.set_image(image):
            return False
        time.sleep(0.1)
        self._send_ctrl_v()
        return True

    def paste_image_from_file(self, file_path: str) -> bool:
        """Load image from file, copy to clipboard, and paste."""
        if not self.set_image_from_file(file_path):
            return False
        time.sleep(0.1)
        self._send_ctrl_v()
        return True

    @staticmethod
    def _send_ctrl_v() -> None:
        """Send Ctrl+V keystroke."""
        user32.keybd_event(0x11, 0, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(0x56, 0, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(0x56, 0, 2, 0)
        user32.keybd_event(0x11, 0, 2, 0)
