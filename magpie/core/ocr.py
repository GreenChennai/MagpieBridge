"""OCR module for text recognition in WeChat screenshots."""

from __future__ import annotations

import difflib
import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from typing import Optional

from PIL import Image

logger = logging.getLogger(__name__)

_ocr_engine = None
_ocr_checked = False


@dataclass
class ChatListItem:
    """A single item in the chat list."""
    name: str
    y_position: int
    x_position: int = 0
    width: int = 0
    height: int = 0


@dataclass
class ChatListSnapshot:
    """A snapshot of the chat list at a point in time."""
    items: list[ChatListItem] = field(default_factory=list)
    timestamp: float = 0
    scroll_y: int = 0


def _models_dir() -> Optional[str]:
    """Locate a drop-in PP-OCRv4/v3 `models/` directory (det/rec/cls.onnx).

    The LeadLinker / OCR.exe project ships better PP-OCRv4-det + PP-OCRv3-rec
    ONNX models.  If they exist next to the exe (frozen) or in the repo, we use
    them; otherwise we fall back to rapidocr's bundled defaults.
    """
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(os.path.dirname(sys.executable), "models"))
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, "models"))
    else:
        candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "models"))
    for d in candidates:
        if os.path.isfile(os.path.join(d, "det.onnx")) and os.path.isfile(os.path.join(d, "rec.onnx")):
            return d
    return None


def _get_ocr():
    """Get or initialize the OCR engine (preferring the PP-OCRv4/v3 models)."""
    global _ocr_engine, _ocr_checked
    if _ocr_checked:
        return _ocr_engine
    _ocr_checked = True

    logger.info("Initializing OCR engine...")

    try:
        if getattr(sys, "frozen", False):
            meipass = sys._MEIPASS
            src_config = os.path.join(meipass, "rapidocr_onnxruntime", "config.yaml")

            try:
                import rapidocr_onnxruntime
                pkg_dir = os.path.dirname(rapidocr_onnxruntime.__file__)
                dst_config = os.path.join(pkg_dir, "config.yaml")

                if not os.path.exists(dst_config) and os.path.exists(src_config):
                    shutil.copy2(src_config, dst_config)
                    logger.info("Copied config.yaml to: %s", dst_config)
            except ImportError as e:
                logger.warning("Failed to import rapidocr_onnxruntime: %s", e)

        from rapidocr_onnxruntime import RapidOCR

        models = _models_dir()
        if models:
            logger.info("使用 PP-OCRv4/v3 模型: %s", models)
            _ocr_engine = RapidOCR(
                det_model_path=os.path.join(models, "det.onnx"),
                rec_model_path=os.path.join(models, "rec.onnx"),
                cls_model_path=os.path.join(models, "cls.onnx"),
                use_angle_cls=False,
                text_score=0.35,
                min_height=6,
            )
        else:
            logger.info("未找到本地 PP-OCRv4/v3 模型，使用 rapidocr 默认模型")
            _ocr_engine = RapidOCR(text_score=0.35, min_height=6)
        logger.info("OCR engine initialized successfully")
    except Exception as e:
        logger.warning("Failed to initialize OCR engine: %s", e)
        _ocr_engine = None

    return _ocr_engine


def recognize_glyphs(img: Image.Image, min_score: float = 0.18) -> list[dict]:
    """Recognize individual small glyphs (digit-reply fallback).

    The PP-OCR *detector* drops a lone thin digit like "1", but the *recognition*
    model reads it fine if we give it a tight crop.  So we find small dark
    connected-components (glyphs), crop each with padding, upscale and run the
    recognizer directly (bypassing the detector).  Returns a list of
    {"text","score","bbox"}.

    Used to detect single-character replies ("1"/"0") that the normal OCR misses.
    """
    eng = _get_ocr()
    if eng is None or img is None:
        return []
    try:
        import cv2
        import numpy as np

        rgb = np.array(img.convert("RGB"))
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        # dark glyphs -> white blobs on black (for connectedComponents)
        _, bw = cv2.threshold(gray, 130, 255, cv2.THRESH_BINARY_INV)
        n, _, stats, _ = cv2.connectedComponentsWithStats(bw, 8)

        out: list[dict] = []
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            if not (3 <= w <= 80 and 5 <= h <= 70 and area >= 8):
                continue
            # crop the original (grayscale) glyph, pad it, upscale to 64x64
            crop = gray[int(y):int(y + h), int(x):int(x + w)]
            pad = max(int(w), int(h)) + 4
            canvas = np.full((pad, pad), 255, np.uint8)
            canvas[: h, : w] = crop
            big = cv2.resize(canvas, (64, 64), interpolation=cv2.INTER_LINEAR)
            rgb_in = cv2.cvtColor(big, cv2.COLOR_GRAY2RGB)

            try:
                res = eng.text_recognizer([rgb_in])
            except Exception:
                continue
            if not res or not res[0]:
                continue
            line = res[0][0]
            text = str(line[0]).strip()
            try:
                score = float(line[1])
            except (TypeError, ValueError):
                score = 0.0
            if text and score >= min_score:
                out.append({
                    "text": text,
                    "score": score,
                    "bbox": [(int(x), int(y)), (int(x + w), int(y)), (int(x + w), int(y + h)), (int(x), int(y + h))],
                })
        return out
    except Exception:
        return []


def _preprocess(img: Image.Image) -> Image.Image:
    """Grayscale + contrast/sharpness enhancement for low-contrast screenshots."""
    try:
        from PIL import ImageEnhance, ImageOps
        work = ImageOps.grayscale(img).convert("RGB")
        work = ImageEnhance.Contrast(work).enhance(1.4)
        work = ImageEnhance.Sharpness(work).enhance(1.2)
        return work
    except Exception:
        return img


def ocr_recognize(img: Image.Image, retries: int = 3) -> list[dict]:
    """Recognize text in an image using OCR.

    On empty/failed results it retries up to 3 times with image preprocessing
    (grayscale + contrast), waiting 500ms between attempts - matches the
    "multi-attempt OCR" strategy used against WeChat's visual-layer defenses.

    Returns list of dicts with keys: text, bbox, score
    """
    ocr = _get_ocr()
    if ocr is None:
        return []

    import numpy as np
    import time as _time

    last: list[dict] = []
    for attempt in range(retries):
        try:
            work = _preprocess(img) if attempt > 0 else img
            img_array = np.array(work)
            result, elapse = ocr(img_array)

            texts: list[dict] = []
            if result:
                for item in result:
                    bbox, text, score = item
                    # Newer rapidocr_onnxruntime returns score as str, normalize
                    try:
                        score_f = float(score)
                    except (TypeError, ValueError):
                        score_f = 0.0
                    texts.append({
                        "text": text,
                        "bbox": bbox,
                        "score": score_f,
                    })
            last = texts
            if texts:
                logger.debug("OCR recognized %d text regions", len(texts))
                return texts
        except Exception:
            logger.exception("OCR recognition failed (attempt %d/%d)", attempt + 1, retries)

        if attempt < retries - 1:
            _time.sleep(0.5)

    return last


def scan_chat_list(img: Image.Image, region: tuple[int, int, int, int]) -> list[ChatListItem]:
    """Scan chat list region and return detected items.

    WeChat 4.x chat list row layout (one list item):
        [name]                           15:23 / 星期二   <- title row
        message preview text                              <- preview row (y +~20px)

    Args:
        img: Full WeChat window screenshot (client area)
        region: (x1, y1, x2, y2) absolute coordinates within the screenshot

    Returns:
        List of ChatListItem with name and position (window-client coords)
    """
    x1, y1, x2, y2 = region
    chat_img = img.crop((x1, y1, x2, y2))

    texts = ocr_recognize(chat_img)

    # Group OCR text blocks into visual rows (same y-top within 12px).
    rows: list[list[dict]] = []
    for item in sorted(
        texts,
        key=lambda t: (min(p[1] for p in t["bbox"]), min(p[0] for p in t["bbox"])),
    ):
        y_top = min(p[1] for p in item["bbox"])
        if rows and abs(y_top - rows[-1][0]["y_top"]) <= 12:
            rows[-1].append(item)
        else:
            item["y_top"] = y_top
            rows.append([item])

    items: list[ChatListItem] = []
    prev_title_y: Optional[int] = None  # crop coords of previous accepted title row

    for row in rows:
        blocks = [t for t in row if t["text"].strip()]
        if not blocks:
            continue

        # Split the row into "name region" blocks (left side, ≤200 px, not
        # time/placeholder/unread-badge) and the rest. The previous
        # implementation picked a single left-most block and discarded every
        # other text box in the same row, so names like "H12【芬芬】+神车线."
        # were truncated to "H12【芬芬】" and any subsequent target lookup
        # silently failed.
        name_blocks: list[dict] = []
        for b in blocks:
            text = b["text"].strip()
            if _TIME_DATE.match(text):
                continue
            if _MSG_PLACEHOLDER.match(text):
                continue
            if _UNREAD_BADGE.match(text):
                continue
            if _is_noise_symbol(text):
                # OCR occasionally turns the row's separator icon / underline
                # into "-" / "—" / "·". That's never a chat name; skip.
                continue
            if min(p[0] for p in b["bbox"]) >= 200:
                continue
            name_blocks.append(b)

        if not name_blocks:
            continue

        name_blocks.sort(key=lambda b: min(p[0] for p in b["bbox"]))
        text = _merge_name_blocks([b["text"].strip() for b in name_blocks]).strip()
        if not text:
            continue

        cand_y = min(min(p[1] for p in b["bbox"]) for b in name_blocks)

        # Preview row: ~20px below its title row -> belongs to previous item
        if prev_title_y is not None and cand_y - prev_title_y < 30:
            continue

        xs = [p[0] for t in row for p in t["bbox"]]
        ys = [p[1] for t in row for p in t["bbox"]]

        items.append(ChatListItem(
            name=text,
            x_position=int((min(xs) + max(xs)) / 2) + x1,
            y_position=int((min(ys) + max(ys)) / 2) + y1,
            width=int(max(xs) - min(xs)),
            height=int(max(ys) - min(ys)),
        ))
        prev_title_y = cand_y

    items.sort(key=lambda x: x.y_position)
    logger.debug("OCR scanned %d items from chat list", len(items))
    return items


# Pure time/date text that may appear at the right side of a row
_TIME_DATE = re.compile(
    r"^\d{1,2}[:：]\d{2}$|^星期[一二三四五六日天]$|^周[一二三四五六日天]$|"
    r"^(昨天|今天|前天|上午|下午|晚上|中午|凌晨)$"
)
# WeChat message-preview placeholders: "[3条]", "[草稿]", "[图片]", "[304条】xx.."
_MSG_PLACEHOLDER = re.compile(
    r"^\[\d+[条】]|^\[(草稿|图片|表情|视频|语音|链接|文件|位置|红包|转账|名片|小程序|音乐|动画表情|系统消息)[】\]]"
)
# Unread-badge style: a tiny number right next to the name (e.g. "3" in red).
# We don't want to glue a stray digit into the contact name, so drop
# short all-digit blocks regardless of x position.
_UNREAD_BADGE = re.compile(r"^\d{1,3}$")

# Pure-symbol/punctuation noise from the OCR engine on a wide crop region.
# PP-OCR occasionally picks up UI artefacts (chat-bubble dividers, icon glyphs,
# underline marks) as short strings like "-" / "—" / "─" / "·" / "|" / ".." /
# "—". These are not contact names; if we kept them, the chat-title reader
# (and even scan_chat_list on its bad days) would surface "-" as a name.
# Keep anything that contains at least one CJK character or an ASCII
# alphanumeric letter — those are real text, not decoration.
def _is_noise_symbol(text: str) -> bool:
    if not text:
        return True
    if any("\u4e00" <= ch <= "\u9fff" for ch in text):
        return False
    if any(ch.isalnum() and ord(ch) < 128 for ch in text):
        return False
    return True


def _merge_name_blocks(texts: list[str]) -> str:
    """Concatenate adjacent name-region text blocks in left-to-right order,
    removing any 1-3 char overlap the OCR detector may have produced.

    Why overlap removal: PP-OCR sometimes splits a name like "安信德" at a
    boundary it can't decide on, returning ["安信德", "德＆创客龙 金陵"] with
    a duplicated "德" — naive concat would yield "安信德德＆创客龙 金陵".
    Detecting the longest trailing/leading substring (≤3 chars) common to
    the seam fixes this without a real OCR model rewrite.
    """
    if not texts:
        return ""
    out = texts[0]
    for nxt in texts[1:]:
        best = 0
        for k in range(min(3, len(out), len(nxt)), 0, -1):
            if out[-k:] == nxt[:k]:
                best = k
                break
        out = out + nxt[best:]
    return out


def find_contact_by_name(
    items: list[ChatListItem],
    target_name: str,
    allow_fuzzy: bool = True,
) -> Optional[ChatListItem]:
    """在扫描到的会话项中定位目标会话。

    匹配口径全部委托给 :mod:`target_guard`，核心变化是**取消了双向子串匹配**。

    .. warning::
        旧实现的 ``target in item_name or item_name in target`` 会让目标
        「客户群」命中「客户群2」、目标「张三」命中「张三丰」，并返回列表里
        第一个命中项 —— 这是"稳定发错人"的根因。现在：

        * 精确匹配（归一化后相等）优先；
        * 模糊匹配**必须唯一**，命中多项直接返回 None（宁可不发）；
        * 目标是候选的前缀（「客户群」⊂「客户群2」）明确拒绝。

    Args:
        allow_fuzzy: 为 False 时只接受精确匹配（用于发送前的守卫复查）。
    """
    from .target_guard import match_reason, name_matches, normalize

    target = target_name.strip()
    if not target:
        return None

    target_norm = normalize(target)

    exact = [i for i, it in enumerate(items) if normalize(it.name) == target_norm]
    if exact:
        if len(exact) > 1:
            logger.warning(
                "目标「%s」精确匹配到 %d 个同名会话，取最靠上的一个",
                target, len(exact),
            )
        return items[exact[0]]

    if not allow_fuzzy:
        logger.warning(
            "严格模式：%d 个会话中没有与「%s」精确匹配的项，放弃定位",
            len(items), target,
        )
        return None

    fuzzy = [(i, it) for i, it in enumerate(items) if name_matches(it.name, target)]
    if not fuzzy:
        if items:
            best = difflib.SequenceMatcher
            closest = max(items, key=lambda it: best(None, normalize(it.name), target_norm).ratio())
            logger.warning(
                "未在 %d 个会话中找到「%s」（最接近「%s」：%s）",
                len(items), target, closest.name, match_reason(closest.name, target),
            )
        else:
            logger.warning("会话列表为空，无法定位「%s」", target)
        return None

    if len(fuzzy) > 1:
        # 歧义：宁可放弃，也不要赌一个发错人
        preview = "、".join(it.name for _, it in fuzzy[:5])
        logger.error(
            "目标「%s」模糊匹配到 %d 个会话（%s），存在歧义，拒绝定位以避免发错人",
            target, len(fuzzy), preview,
        )
        return None

    idx, item = fuzzy[0]
    logger.warning(
        "目标「%s」未精确命中，模糊匹配到「%s」（%s），请确认是否为预期会话",
        target, item.name, match_reason(item.name, target),
    )
    return item
