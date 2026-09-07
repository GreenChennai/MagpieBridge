"""Unread red-dot / badge detection for WeChat chat list items.

WeChat 4.x shows two kinds of notification badge on a chat-item's avatar:

- **Unread badge** ("大红点+数字"): a filled red badge holding a number
  (e.g. 1, 12, 99+). The chat really has unread messages -> we should open it.
- **Muted badge** ("小红点+无数字"): a small, empty red dot with NO number.
  The chat has 消息免打扰 (message mute) turned on -> skip it (it would send
  a tiny dot but no sound and no unread count we care about).

"None": no badge at all -> nothing to do.

We detect red pixels via PIL, then OCR the badge area to read the count, and
classify the badge into one of the three kinds.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from PIL import Image

from .ocr import ChatListItem, ocr_recognize

logger = logging.getLogger(__name__)


@dataclass
class BadgeInfo:
    """Classification of a chat-item notification badge."""

    kind: str  # "unread" | "muted" | "none"
    count: int = 0   # unread count when kind == "unread"
    area: int = 0    # number of red pixels found

    @property
    def is_unread(self) -> bool:
        return self.kind == "unread"

    @property
    def is_muted(self) -> bool:
        return self.kind == "muted"


def _is_red(r: int, g: int, b: int) -> bool:
    """WeChat badge red: strongly red, weak blue channel."""
    return r > 150 and g < 120 and b < 120 and (r - g) > 30


def _red_pixel_count(img: Image.Image, box: tuple[int, int, int, int]) -> int:
    """Count red pixels in box (x0, y0, x1, y1), returns 0 if box invalid."""
    x0, y0, x1, y1 = box
    w = max(x1 - x0, 1)
    h = max(y1 - y0, 1)
    region = img.crop((x0, y0, x1, y1)).resize((min(w, 80), min(h, 40)))
    px = region.load()
    count = 0
    for yy in range(region.height):
        for xx in range(region.width):
            r, g, b = px[xx, yy][:3]
            if _is_red(r, g, b):
                count += 1
    return count


def _raw_red_stats(img: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, tuple[int, int, int, int]]:
    """Count red pixels RAW (no resize) and return the tight red bounding box.

    Returns (raw_count, (minx, miny, maxx, maxy)) where coords are local to
    `box`.  The raw pixel count / bbox size is what lets us tell a big "大红点"
    unread badge apart from a tiny 免打扰 red dot (resizing would inflate both).
    """
    x0, y0, x1, y1 = box
    region = img.crop((x0, y0, x1, y1))
    px = region.load()
    count = 0
    minx = miny = 10 ** 9
    maxx = maxy = -1
    h, w = region.height, region.width
    for yy in range(h):
        for xx in range(w):
            r, g, b = px[xx, yy][:3]
            if _is_red(r, g, b):
                count += 1
                if xx < minx:
                    minx = xx
                if xx > maxx:
                    maxx = xx
                if yy < miny:
                    miny = yy
                if yy > maxy:
                    maxy = yy
    if count == 0:
        return 0, (0, 0, 0, 0)
    return count, (minx, miny, maxx, maxy)


def _ocr_count(img: Image.Image, box: tuple[int, int, int, int]) -> Optional[int]:
    """OCR digits inside a badge box. Returns None when no digits found.

    We crop generously and upscale aggressively (badges are ~16-22px); also try
    a grayscale-boosted pass so tiny white digits on the red badge read better.
    """
    x0, y0, x1, y1 = box
    # crop wider to capture the whole badge (numbers may hug the edge)
    badge_img = img.crop((max(x0 - 6, 0), max(y0 - 4, 0), min(x1 + 6, img.width), min(y1 + 4, img.height)))

    if badge_img.width < 12 or badge_img.height < 8:
        return None

    target_w = 96
    scale = max(3, target_w // max(badge_img.width, 1))
    badge_img = badge_img.resize(
        (badge_img.width * scale, badge_img.height * scale),
        Image.LANCZOS,
    )

    candidates = [badge_img]
    try:
        from PIL import ImageOps
        # brighten / boost contrast to make the number legible
        boosted = ImageOps.autocontrast(badge_img.convert("L")).convert("RGB")
        candidates.append(boosted)
    except Exception:
        pass

    for candidate in candidates:
        try:
            texts = ocr_recognize(candidate)
        except Exception:
            return None
        for t in texts:
            text = (t.get("text") or "").strip()
            m = re.fullmatch(r"(\d{1,3})\+?", text)
            if m:
                return int(m.group(1))
    return None


def detect_total_unread_badge(screenshot: Image.Image) -> Optional[int]:
    """Detect the total unread badge on the left sidebar (WeChat icon).

    WeChat 4.x shows a red badge with a number on the left sidebar icon.
    Returns None when no badge, otherwise the total unread count.
    """
    px = screenshot.load()
    badge_area = (15, 85, 55, 125)
    red_count = _red_pixel_count(screenshot, badge_area)

    if red_count < 10:
        return None

    count = _ocr_count(screenshot, badge_area)
    if count is not None:
        logger.info("检测到总未读数: %d", count)
        return count

    # If OCR failed but we found red pixels, assume count is 1
    logger.info("检测到未读红点（无数字）")
    return 1


def classify_item_badge(
    screenshot: Image.Image,
    item_x: int,
    item_y: int,
    item_width: int = 60,
    item_height: int = 50,
    img: Optional[Image.Image] = None,
) -> BadgeInfo:
    """Classify the notification badge of a chat item.

    The badge sits near the avatar's top-right corner, to the LEFT of the
    contact-name text block. We scan a broad band across the left half of the
    row, find the densest red cluster, then classify by size + OCR:

    - no red cluster                              -> "none"
    - large red cluster (filled badge)            -> "unread" (count from OCR,
                                                    or 1 if the digit can't be read)
    - tiny red dot with no digit (免打扰)          -> "muted"

    The key fix: an unread badge whose digit OCR can't read must STILL be
    treated as unread (it's a *big* red badge), never as a muted dot.
    """
    source = img if img is not None else screenshot

    # Broad band spanning the avatar/badge strip (far left of the row, to the
    # LEFT of the name).  The avatar sits at the window's left edge (~x=70-110),
    # while item_x is the *name* center (~x=220), so we must reach far left.
    x0 = max(25, int(item_x - 150))
    x1 = min(source.width, int(item_x + 12))
    y0 = max(0, item_y - 22)
    y1 = min(source.height, item_y + 20)

    win_w, win_h = 38, 24
    best_count = 0
    best_box = (max(x0, 0), max(y0, 0), min(x1, source.width), min(y1, source.height))
    best_bbox = (0, 0, 0, 0)
    step = 6
    for cy in range(y0, max(y0 + 1, y1 - win_h + 1), step):
        for cx in range(x0, max(x0 + 1, x1 - win_w + 1), step):
            box = (cx, cy, min(cx + win_w, x1), min(cy + win_h, y1))
            count, bbox = _raw_red_stats(source, box)
            if count > best_count:
                best_count = count
                best_box = box
                best_bbox = bbox

    if best_count < 4:
        return BadgeInfo(kind="none", area=best_count)

    # tight red area = real badge size (unaffected by upscaling)
    bw = max(best_bbox[2] - best_bbox[0] + 1, 0)
    bh = max(best_bbox[3] - best_bbox[1] + 1, 0)
    tight_area = bw * bh

    # OCR the cluster for a digit.
    count = _ocr_count(source, best_box)
    if count is not None:
        logger.info("Chat item badge: %d unread (area=%dx%d)", count, bw, bh)
        return BadgeInfo(kind="unread", count=count, area=tight_area)

    # No readable digit: a big filled '大红点' is unread; a tiny dot is muted.
    # A real unread badge is >= ~14px (area ~150+); a 免打扰 dot is ~6-8px
    # (area ~36-64).  Use a middle threshold so only real badges count.
    if tight_area >= 100:
        logger.info("Chat item badge: 大红点（数字未识别，视为未读）area=%dx%d", bw, bh)
        return BadgeInfo(kind="unread", count=1, area=tight_area)

    logger.info("Chat item badge: 小红点无数字（免打扰，跳过）area=%dx%d", bw, bh)
    return BadgeInfo(kind="muted", area=tight_area)
