# -*- coding: utf-8 -*-
"""LeadsLinker 图片渲染模块（文件夹插件演示：从 __init__ 拆分出的子模块）。

渲染 HTML 线索卡片为图片：
1. 优先 playwright（Chromium 截图，效果最好）
2. playwright 不可用时降级 Pillow（纯色卡片 + 文本）

v3.4.0 修复：
- 不再使用 `wait_until="networkidle"`（外部字体 CDN 可能永远拉不到 -> 卡死），
  改为 `domcontentloaded` 并附带 8s 超时。
- 及时关闭浏览器实例（finally），避免句柄泄漏。
- 使用 logging 输出，而非 print（避免污染 MagpieBridge TUI 终端）。

v3.4.2 修复（重要）：
- 修正 Pillow 降级路径正则匹配 class 名：
  v3.4.0 重构 contact-block 时把 `info-value` 改为 `contact-value`，
  但 renderer.py 未同步，导致 Pillow 降级渲染的图只有姓名，
  缺少电话/微信/备注——**生产环境未装 playwright 走 Pillow 时真实图片不完整**。
  本次更新 class 名 + 备注在 `.note-row` 内的结构。

v4.2.0 美化（图片排版升级）：
- @消息（引擎层，__init__.py）改为携带主要消息 + 联系方式（手机号/微信号）。
- Pillow 降级渲染整体重做：渐变横幅 header、圆角卡片、首字头像、
  类型徽章、电话/微信彩色高亮、二维码内嵌（base64/URL）、备注块、回复指引 footer，
  字段与文字版一致且排版更美观；多线索纵向堆叠、高度动态。
- 注意：Pillow 图形中不使用 emoji（微软雅黑无 emoji 字形会画成方框），
  一律用彩色图形元素（圆点/色块/徽章）代替。
- HTML 版（playwright 优先路径）同步微调信息一致性。

v4.3.0 现代感美化：
- header 升级为三色渐变（indigo→violet→fuchsia）+ 右侧装饰圆环。
- 线索卡：圆角 20 + 细边框线（替代无边框），头像改双环渐变（外浅紫环+内渐变圆）+ 白色首字。
- 联系块/二维码/footer 圆角统一 16，二维码白底圆角 + 细边框。
- 画布背景改浅紫灰 #f4f5fb，标签灰/次要文字色微调更柔和。
- 发送顺序（__init__.py）：image = ①图片 → ②联系方式文字 → ③@人；
  text = ①主要文字 → ②@人。

v4.4.0 收敛美化（向"HTML版好看的那版"对齐）：
- 头像去复杂化：移除 19 层双环渐变，改回"浅紫平涂底 (#eef2ff) + 紫色首字 (#6366f1)"，
  外加 2px 浅紫描边圈，整体更清爽、更现代。
- 二维码 inline 化：从右上角独立方块移入联系方式块内（如果存在 qrcode），
  顶部一行排布（icon | "二维码" + 提示 | QR图 + "微信扫一扫"），
  更紧凑，与 HTML 漂亮版对齐。
- header icon 简化：原"白方块+实心紫铃铛"图标改为"半透明浅紫方块 + 简笔喇叭"
  （Pillow 无 emoji 用极简几何图形模拟，效果柔和不抢戏）。
- footer 数字高亮："X 分钟"、"1"、"0"、受理/跳过 用亮绿 #6ee7b7 单独画，
  对应 HTML 版 `<b>` 标签。

v4.5.0 Pillow 渲染质量提升（用户报告"不够美观"复盘 + 修复）：
- **致命修复**：`_extract_field` 提取 HTML 字段后立即 `html.unescape()`，
  修复 `&amp;` 等 HTML 实体被字面量画出的问题（如 "安信德&创客龙" → "安信德&amp;创客龙"）。
- **header 装饰圆裁切修复**：原 `ellipse([..., y-36, ..., y+24])` 圆心 y 起点
  在 header 顶之上，导致右上半圆被切到画布外；改为完全位于 header 内的双层柔光圆
  （外层 12% 白色混合 + 内层 22% 白色混合，y 范围 HEADER_H/2±24）。
- **header icon 重画**：pieslice 180-360 在 PIL 中实际画出整圆（角度歧义），
  改为显式 polygon 梯形铃身 + 矩形挂环 + 紫色铃舌，"通知"图形识别度大幅提升。
- **头像首字对比度**：从"浅紫底 + 紫字"改为"紫渐变底 + 白色首字 + 加粗"，
  与 header icon 风格一致，姓名辨识度更强。
- **二维码区文字对齐**：原三行文字 x 起点（110/124/110）混用、字号 11/13 不一；
  改为统一 x 起点 + 14px 粗体"二维码"标题 + 12px 两行提示，
  视觉层级更清晰。

v4.6.0 布局收敛（用户反馈"头像有方有圆 / 头像上横线 / footer 圆角异常"）：
- **删除卡片顶部 4px 渐变条**：该色条悬在头像正上方（x28-68, y=card_top..+5），
  视觉上像"头像上面一条奇怪的横线"，整条移除，卡片顶部恢复干净白底。
- **头像重画为单一真圆渐变**：原实现"48px 外环 ellipse + 42 条整宽水平线渐变"——
  逐行 draw.line 形成 42×42 **方形渐变底**嵌在圆环内，出现"方 + 圆"叠层；
  改为逐行按圆弦宽绘制（math.sqrt 裁切），得到真正的圆形纵向渐变
  （indigo→violet），去掉外环描边，头像 = 单个圆 + 白色加粗首字（垂直居中）。
- **footer 圆角真正生效**：原实现先 _vgradient 画直角渐变矩形、再
  rounded_rectangle(fill=None) 只描边 → 四角仍是直角、顶部残留白色锯齿
  （"像圆角又是方的"）。改为在独立 RGB 图层画渐变 + L 模式圆角 mask 裁切，
  再 img.paste 回画布，四角平滑内凹、无白边。

v4.7.0 圆角抗锯齿 + 顶部组件收敛（用户反馈"圆角有锯齿 / 标签英文 / 时间太小 / 头像字偏下"）：
- **几何形状全局抗锯齿**：ImageDraw 的 rounded_rectangle/ellipse/polygon 在 1:1
  分辨率下**无抗锯齿**，圆角弧线呈 1px 阶梯（"不圆润、锯齿感"）。新增
  `_paste_rounded_aa` / `_paste_ellipse_aa`：在 ss 倍分辨率 RGBA 图层绘制 →
  LANCZOS 缩回 → alpha 贴回，边缘产生平滑过渡像素。卡片圆角底+描边、联系块、
  备注块、二维码白底、badge 胶囊、序号 chip、header 装饰圆、header icon 整块、
  footer 圆角 mask（4x）、绿脉冲点、联系色点全部换用 AA 绘制。
- **badge 文字中文化**（HTML 模板 `_build_image_content`）：lead/ad/biz 等英文
  类型值（上游来客 type 可能为 "lead"/"AD"）映射为中文（线索/广告/经营线索），
  并让 badge 配色判定兼容英文输入。
- **来客时间放大**：卡片顶部 UI 组件（lead-top）内时间行 11px → 13px（与联系
  方式 label 同级），颜色 _C_SUB 浅灰加深为 #64748b；序号 chip 微调对齐。
- **头像首字真正居中**：原用 `(avatar_size-21)/2` 以字号近似字高估 y → 字形视觉
  偏下。现头像整体画进 4x 图层，首字用 `anchor="mm"`（按字形 bbox）精确水平
  垂直居中，缩小贴回后文字居中且平滑。

v4.7.1 关键 bug 修复（用户反馈"头像圆角有白色锯齿、背景不透是黑色矩形"）：
- **根因**：v4.7.0 把 header icon 与头像画进 RGBA 图层后，`img.paste(layer, (x, y))`
  **未传第三参 mask** → PIL 忽略 alpha 通道，直接把 RGB 覆盖；透明区 (0,0,0,0)
  的黑色像素被贴到画布上 → 图标/头像周围出现**黑色矩形**，圆角外透不出卡片
  白底，边缘半透明混色呈现"白色锯齿"。
- **修复**：`img.paste(layer, pos, layer)` —— 用图层自身 alpha 作 mask，
  透明区域不覆盖，背景正确透出。（_paste_rounded_aa / _paste_ellipse_aa 一直
  是对的，只有 icon 与头像两处直接 paste 漏了 mask。）
- 像素验证：修复前头像区 304 / icon 区 68 个近黑像素（黑矩形），修复后均为 0。

v4.8.0 渲染器路线切换（用户拍板"接 Edge headless + CSS 重设计模板"）：
- **render_html_to_image 三级回退**：① 系统自带 Edge headless（首选，Windows
  10/11 均有，--headless=new + DPR=2 截图 + 底部留白裁剪 + LANCZOS 缩回 520 宽）
  → ② playwright（开发机路径）→ ③ Pillow 兜底（字段提取正则契约与 class 锚点
  保持不变，见 _extract_cards/_extract_field）。生产 exe 无需打包任何浏览器。
- 渲染质量差异根因：Pillow 是纯绘图库，无 CSS/字体渲染/真实抗锯齿（v4.7.x 的
  超采样 AA 只是模拟）；HTML/CSS 模板重设计见 __init__.py `_build_image_content`
  （v4.8.0 全新浅色商务风格，字段 class 契约保留供 Pillow 兜底）。
"""

import asyncio
import logging
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from html import unescape as _html_unescape

logger = logging.getLogger("leads_forwarder.renderer")


def escape_html(text):
    if not text:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _find_wpi_cli():
    """定位 WPI-noGUI-cli.exe(GreenChennai/WPI, 网页→PNG 导出器, ADR-0008)。

    查找顺序: 环境变量 MAGPIE_WPI → exe 旁 tools/WPI/ → 仓库 tools/WPI/ → PATH。
    找不到返回 None(上层自动回退 Edge/playwright/Pillow)。
    """
    cands = []
    env = os.environ.get("MAGPIE_WPI")
    if env:
        cands.append(env)
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 插件根的上级(开发态仓库根)
    for b in (os.path.dirname(base), base):
        cands.append(os.path.join(b, "tools", "WPI", "WPI-noGUI-cli.exe"))
        cands.append(os.path.join(b, "WPI", "WPI-noGUI-cli.exe"))
    try:
        exe_dir = os.path.dirname(sys.executable)
        cands.insert(0, os.path.join(exe_dir, "tools", "WPI", "WPI-noGUI-cli.exe"))
        cands.insert(0, os.path.join(exe_dir, "WPI-noGUI-cli.exe"))
    except Exception:
        pass
    for p in cands:
        if p and os.path.isfile(p):
            return p
    import shutil
    return shutil.which("WPI-noGUI-cli")


def _render_with_wpi(html_content, wpi_path, timeout=60):
    """用 WPI-noGUI-cli 渲染 HTML → PNG 字节（同步阻塞, 调用方丢线程池）。

    流程: HTML 写临时文件 → `WPI-noGUI-cli --source card.html --output card.png
    --width 520 --scale 2`(整页导出) → 读回 PNG 字节。WPI 自带无头浏览器,
    渲染质量与 Edge 一致。
    """
    with tempfile.TemporaryDirectory(prefix="lf_wpi_") as td:
        html_path = os.path.join(td, "card.html")
        png_path = os.path.join(td, "card.png")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        proc = subprocess.run(
            [wpi_path, "--source", html_path, "--output", png_path,
             "--width", "520", "--scale", "2"],
            capture_output=True, text=True, timeout=timeout,
        )
        out = os.path.join(td, "card.png")
        if proc.returncode != 0 or not os.path.isfile(out):
            logger.warning("[LeadsLinker] WPI 退出码=%s 输出缺失: %s",
                           proc.returncode, (proc.stderr or proc.stdout or "")[:200])
            return None
        with open(out, "rb") as f:
            data = f.read()
        logger.info("[LeadsLinker] WPI 渲染成功: %d bytes", len(data))
        return data if data else None


def _find_edge_path():
    """定位系统自带 Edge（Chromium 内核）。

    v4.8.0：生产 exe 不含 playwright/Chromium，Pillow 纯绘图画质天花板低。
    Windows 10/11 系统自带 Edge，可用 --headless=new 渲染同一 HTML ——
    浏览器级 CSS/渐变/字体/抗锯齿，无需打包浏览器、零额外依赖。
    找不到（如 Windows Server 精简版）时返回 None，由上层回退。
    """
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _render_with_edge(html_content, edge_path, viewport_h=2400):
    """用 Edge headless 渲染 HTML → PNG 字节（同步阻塞，调用方应丢到线程池）。

    流程：HTML 写临时文件 → `msedge --headless=new` DPR=2 截图（视口高度给足）
    → PIL 从底部向上裁剪背景留白 → LANCZOS 缩回 520 宽 CSS 尺寸。
    独立 --user-data-dir 避免与真实 Edge 实例 profile 锁冲突。
    """
    from io import BytesIO
    from PIL import Image

    with tempfile.TemporaryDirectory(prefix="lf_edge_") as td:
        html_path = os.path.join(td, "card.html")
        out_png = os.path.join(td, "shot.png")
        prof_dir = os.path.join(td, "profile")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        cmd = [
            edge_path,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            "--disable-extensions",
            "--disable-sync",
            f"--user-data-dir={prof_dir}",
            "--force-device-scale-factor=2",
            f"--window-size=520,{viewport_h}",
            "--virtual-time-budget=2500",
            f"--screenshot={out_png}",
            pathlib.Path(html_path).as_uri(),
        ]
        proc = subprocess.run(
            cmd, capture_output=True, timeout=40,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        del proc  # 仅需确保进程退出；异常由上层回退
        if not os.path.exists(out_png):
            raise RuntimeError("Edge 未产出截图文件")
        img = Image.open(out_png)
        if img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size  # DPR=2：物理像素 = CSS × 2
        px = img.load()
        # 从底部向上找最后一行"有暗色内容"的行（footer 深蓝条 #232a6b 等），
        # 其下是 body 背景渐变 / viewport 留白 → 整体裁掉。
        # 注意：不能用"近白/近背景"反向判定——body 渐变浅色（如 #eef0f8）每通道
        # 只有 236-240，容易误判为内容；改找亮度总和明显低的深色像素。
        content_bottom = 0
        for yy in range(h - 1, -1, -1):
            step = max(1, (w - 80) // 16)
            row_hit = False
            for xx in range(40, w - 40, step):
                r, g, b = px[xx, yy][:3]
                if r + g + b < 620:  # 深色内容（footer/文字/渐变 header 远低于此）
                    row_hit = True
                    break
            if row_hit:
                content_bottom = yy
                break
        if content_bottom <= 0:
            content_bottom = h - 1
        # 保留 footer 下方约 20px CSS(=40 物理)的背景呼吸边，避免圆角贴地
        crop_h = min(h, content_bottom + 40)
        img = img.crop((0, 0, w, crop_h))
        css_w = 520
        css_h = max(1, round(crop_h / 2.0))
        img = img.resize((css_w, css_h), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="PNG")
        logger.info("[LeadsLinker] Edge headless 渲染完成: %dx%d", css_w, css_h)
        return buf.getvalue()


async def render_html_to_image(html_content):
    # v3.0.2 四级回退：WPI-noGUI-cli(用户指定, ADR-0008) → Edge headless →
    # playwright → Pillow。WPI 是 GreenChennai/WPI 的命令行网页导出器,
    # 输出质量与 Edge 相同且免装浏览器; exe 放 exe 旁 tools/WPI/ 即自动启用。
    wpi = _find_wpi_cli()
    if wpi:
        try:
            png = await asyncio.to_thread(_render_with_wpi, html_content, wpi)
            if png:
                return png
            logger.warning("[LeadsLinker] WPI 渲染未产出文件, 回退 Edge/playwright/Pillow")
        except Exception as e:
            logger.warning("[LeadsLinker] WPI 渲染失败(%s), 回退 Edge/playwright/Pillow", e)

    edge = _find_edge_path()
    if edge:
        try:
            return await asyncio.to_thread(_render_with_edge, html_content, edge)
        except Exception as e:
            logger.warning("[LeadsLinker] Edge headless 渲染失败(%s), 回退 playwright/Pillow", e)

    try:
        from playwright.async_api import async_playwright

        browser = None
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, timeout=15000)
                page = await browser.new_page(viewport={"width": 600, "height": 800})
                # domcontentloaded + explicit timeout: only wait for the local HTML,
                # never block on an external font CDN that may never resolve.
                await page.set_content(html_content, wait_until="domcontentloaded", timeout=8000)
                await page.wait_for_timeout(400)

                card = await page.query_selector(".card")
                if card:
                    screenshot_bytes = await card.screenshot(type="png")
                else:
                    screenshot_bytes = await page.screenshot(type="png", full_page=True)
                return screenshot_bytes
        finally:
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass
    except ImportError:
        logger.info("playwright 未安装, 使用 Pillow 生成图片")
        return await _render_with_pillow(html_content)
    except Exception as e:
        logger.warning("图片渲染失败(%s), 使用 Pillow 生成图片", e)
        return await _render_with_pillow(html_content)


# ---------------------------------------------------------------------------
# Pillow 降级渲染（生产环境无 playwright 时实际路径）
# ---------------------------------------------------------------------------

# 主题色（与 HTML 版保持一致）
_C_HEADER_1 = (99, 102, 241)     # #6366f1 indigo-500
_C_HEADER_2 = (139, 92, 246)     # #8b5cf6 violet-500
_C_HEADER_3 = (217, 70, 239)     # #d946ef fuchsia-500（三色渐变端点）
_C_FOOTER_1 = (30, 27, 75)      # #1e1b4b
_C_FOOTER_2 = (49, 46, 129)     # #312e81
_C_PHONE = (79, 70, 229)        # 电话高亮
_C_WECHAT = (7, 193, 96)        # 微信高亮
_C_AVATAR = (99, 102, 241)      # 头像字色（v4.4.0：紫字）
_C_AVATAR_BG = (224, 231, 255)  # 头像底色 #e0e7ff（v4.4.0：浅紫平涂）
_C_AVATAR_RING = (196, 181, 253)  # 头像描边 #c4b5fd
_C_TEXT = (30, 27, 75)          # 主文字
_C_SUB = (148, 155, 170)        # 次要文字 #94a3b8
_C_LABEL = (139, 139, 167)      # 标签 #8b8ba7
_C_BLOCK_BG = (238, 242, 255)   # 联系块底 #eef2ff
_C_NOTE_BG = (250, 250, 252)    # 备注底 #fafafc
_C_HILITE = (110, 231, 183)     # footer 数字高亮 #6ee7b7（v4.4.0 新增）
_C_LINE = (225, 227, 241)       # 卡片细边框 #e1e3f1
_C_BG = (244, 245, 251)         # 画布底 #f4f5fb

_BADGE_COLORS = {
    "lead": ((22, 163, 74), (220, 252, 231)),     # 绿
    "ad": ((37, 99, 235), (219, 234, 254)),       # 蓝
    "biz": ((217, 119, 6), (254, 243, 199)),      # 橙
    "unknown": ((107, 114, 128), (243, 244, 246)) # 灰
}

_FONT_CACHE = {}


def _get_font(bold=False, size=20):
    from PIL import ImageFont
    key = (bold, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    # 注：msyhbd.ttc 在本机 Pillow 加载时丢字（疑 TTC face index 问题），
    # 统一使用 msyh.ttc；粗体视觉差异不显著，避免字体兼容问题。
    names = [r"C:\Windows\Fonts\msyh.ttc", "msyh.ttc"]
    font = None
    for name in names:
        try:
            font = ImageFont.truetype(name, size)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def _lerp_color(c1, c2, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def _vgradient(draw, x0, y0, x1, y1, c1, c2):
    """垂直渐变填充矩形区域。"""
    h = y1 - y0
    if h <= 0:
        return
    for y in range(h):
        draw.line([(x0, y0 + y), (x1, y0 + y)], fill=_lerp_color(c1, c2, y / max(h - 1, 1)))


def _rounded(draw, xy, radius, **kw):
    """圆角矩形，兼容旧版 Pillow（无 rounded_rectangle 时退化）。"""
    try:
        draw.rounded_rectangle(xy, radius=radius, **kw)
    except AttributeError:
        draw.rectangle(xy, **kw)


def _paste_rounded_aa(img, box, radius, ss=4, fill=None, outline=None, width=1):
    """抗锯齿圆角矩形：在 ss 倍分辨率图层绘制后 LANCZOS 缩回，经 alpha 平滑贴回。

    v4.7.0：ImageDraw.rounded_rectangle / ellipse 在 1:1 分辨率下**没有抗锯齿**，
    圆角弧线会呈现 1px 阶梯（"圆角不圆润、有锯齿感"）。先放大到 ss 倍绘制、
    再 LANCZOS 缩小，边缘会带出平滑的半透明过渡像素。
    """
    from PIL import Image, ImageDraw
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0 or (fill is None and outline is None):
        return
    layer = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    kw = {}
    if fill is not None:
        kw["fill"] = tuple(fill) + (255,)
    if outline is not None:
        kw["outline"] = tuple(outline) + (255,)
        kw["width"] = max(1, int(round(width * ss)))
    try:
        ld.rounded_rectangle([0, 0, w * ss - 1, h * ss - 1], radius=radius * ss, **kw)
    except AttributeError:
        ld.rectangle([0, 0, w * ss - 1, h * ss - 1], **kw)
    layer = layer.resize((w, h), Image.LANCZOS)
    img.paste(layer, (x0, y0), layer)


def _paste_ellipse_aa(img, box, ss=4, fill=None, outline=None, width=1):
    """抗锯齿椭圆/圆（与 _paste_rounded_aa 同思路）。"""
    from PIL import Image, ImageDraw
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0 or (fill is None and outline is None):
        return
    layer = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    kw = {}
    if fill is not None:
        kw["fill"] = tuple(fill) + (255,)
    if outline is not None:
        kw["outline"] = tuple(outline) + (255,)
        kw["width"] = max(1, int(round(width * ss)))
    ld.ellipse([0, 0, w * ss - 1, h * ss - 1], **kw)
    layer = layer.resize((w, h), Image.LANCZOS)
    img.paste(layer, (x0, y0), layer)


def _text_w(draw, text, font):
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]
    except Exception:
        try:
            return draw.textsize(text, font=font)[0]
        except Exception:
            return len(text) * font.size // 2


def _load_qrcode(qrcode_src):
    """加载二维码图片：base64 data URL 或 http(s) URL。失败返回 None。"""
    if not qrcode_src:
        return None
    from io import BytesIO
    from PIL import Image
    try:
        if isinstance(qrcode_src, str) and qrcode_src.startswith("data:image"):
            import base64
            b64 = qrcode_src.split(",", 1)[-1]
            return Image.open(BytesIO(base64.b64decode(b64))).convert("RGB")
        if isinstance(qrcode_src, str) and qrcode_src.startswith("http"):
            import urllib.request
            with urllib.request.urlopen(qrcode_src, timeout=5) as resp:
                return Image.open(BytesIO(resp.read())).convert("RGB")
    except Exception as e:
        logger.info("二维码加载失败: %s", e)
    return None


def _extract_cards(html_content):
    """按 lead-card 分块提取每条线索的 HTML 片段。"""
    parts = html_content.split('<div class="lead-card">')
    cards = []
    for part in parts[1:]:
        card = part.split('<div class="card-footer">')[0]
        cards.append(card)
    return cards


def _extract_field(card, pattern):
    """正则提取字段后立即做 HTML unescape（&amp; → & 等），否则 Pillow 路径会把
    `&amp;` 当字面量画出来。"""
    m = re.search(pattern, card)
    if not m:
        return ""
    return _html_unescape(m.group(1))


async def _render_with_pillow(html_content):
    try:
        from PIL import Image, ImageDraw, ImageFont
        import re

        # ---------- 提取 header / footer 元信息 ----------
        now_str = ""
        m = re.search(r'<div class="subtitle">([^<]+)</div>', html_content)
        if m:
            now_str = m.group(1).strip()

        timeout_minutes = 5
        m = re.search(r"请在 <b>(\d+)分钟</b> 内回复", html_content)
        if m:
            timeout_minutes = int(m.group(1))

        # ---------- 提取每条线索字段 ----------
        leads = []
        for card in _extract_cards(html_content):
            name = _extract_field(card, r'class="lead-name">([^<]+)<')
            serial = _extract_field(card, r'class="lead-serial">([^<]+)<')
            msg_time = _extract_field(card, r'class="lead-time">([^<]+)<')
            badge_cls = _extract_field(card, r'class="lead-type-badge badge-(\w+)">')
            badge_txt = _extract_field(card, r'class="lead-type-badge badge-\w+">([^<]+)<')
            phone = _extract_field(card, r'class="contact-value phone">([^<]+)<')
            wechat = _extract_field(card, r'class="contact-value wechat">([^<]+)<')
            qrcode = _extract_field(card, r'class="qr-img" src="([^"]+)"')
            note = _extract_field(card, r'class="note-row"><span class="note-label">备注</span><span>([^<]+)<')
            leads.append({
                "name": name or "未知客户",
                "serial": serial,
                "time": msg_time,
                "badge_cls": badge_cls if badge_cls in _BADGE_COLORS else "unknown",
                "badge_txt": badge_txt or "线索",
                "phone": phone,
                "wechat": wechat,
                "qrcode": qrcode,
                "note": note,
            })

        # ---------- 布局尺寸 ----------
        W = 520
        PAD = 22
        HEADER_H = 74
        FOOTER_H = 46

        leads = leads or [{"name": "未知客户", "serial": "", "time": "", "badge_cls": "unknown",
                           "badge_txt": "线索", "phone": "", "wechat": "", "qrcode": "", "note": ""}]
        n_leads = len(leads)

        # 预解码二维码（inline 用）
        qr_imgs = []
        for lead in leads:
            qr_imgs.append(_load_qrcode(lead["qrcode"]) if lead["qrcode"] else None)

        # 逐卡计算高度：
        # - 头像区：60（顶部 12 + 头像 52 + 时间行 + 间隔）
        # - 联系块：标题行 30 + 每行 36 + qr 行（如果有）额外 +108（QR 96 + 上下间距）
        # - 备注：46（如果有）
        card_heights = []
        for lead, qr_img in zip(leads, qr_imgs):
            rows = 0
            if lead["phone"]:
                rows += 1
            if lead["wechat"]:
                rows += 1
            qr_extra = 108 if qr_img else 0  # inline 二维码行（QR96 + 上下间距）
            contact_h = 30 + max(rows, 1) * 30 + qr_extra
            note_h = 46 if lead["note"] else 0
            card_heights.append(12 + 60 + 14 + contact_h + 14 + note_h + 12)

        total_h = PAD + HEADER_H + 18 + sum(h + 14 for h in card_heights) + FOOTER_H + PAD

        img = Image.new("RGB", (W, total_h), _C_BG)
        draw = ImageDraw.Draw(img)
        y = PAD

        # ---------- header：三色渐变横幅 + 柔美装饰圆环 ----------
        # 上半 indigo→violet，下半 violet→fuchsia
        _vgradient(draw, PAD, y, W - PAD, y + HEADER_H // 2, _C_HEADER_1, _C_HEADER_2)
        _vgradient(draw, PAD, y + HEADER_H // 2, W - PAD, y + HEADER_H, _C_HEADER_2, _C_HEADER_3)
        # 右侧装饰：双层柔光圆（完全位于 header 内，不裁切；AA 超采样绘制）
        cy_dec = y + HEADER_H // 2
        dec_outer = tuple(int(255 * 0.12 + c * 0.88) for c in _lerp_color(_C_HEADER_2, _C_HEADER_3, 0.5))
        _paste_ellipse_aa(img, [W - PAD - 60, cy_dec - 24, W - PAD - 12, cy_dec + 24], fill=dec_outer)
        dec_inner = tuple(int(255 * 0.22 + c * 0.78) for c in _lerp_color(_C_HEADER_2, _C_HEADER_3, 0.5))
        _paste_ellipse_aa(img, [W - PAD - 44, cy_dec - 12, W - PAD - 20, cy_dec + 12], fill=dec_inner)

        # header icon：白边方块 + 简笔铃铛（整块 4x 超采样，避免小图形锯齿）
        icon_x, icon_y = PAD + 18, y + (HEADER_H - 40) // 2
        _is = 40 * 4
        _icon_layer = Image.new("RGBA", (_is, _is), (0, 0, 0, 0))
        _ild = ImageDraw.Draw(_icon_layer)
        _ild.rounded_rectangle([0, 0, _is - 1, _is - 1], radius=12 * 4,
                               outline=(255, 255, 255, 255), width=2 * 4)
        _ild.rounded_rectangle([2 * 4, 2 * 4, _is - 1 - 2 * 4, _is - 1 - 2 * 4], radius=10 * 4,
                               fill=tuple(int(255 * 0.20 + c * 0.80) for c in _lerp_color(_C_HEADER_1, _C_HEADER_3, 0.6)) + (255,))
        cx, cy_bell = 20 * 4, 20 * 4
        bell_color = _lerp_color(_C_HEADER_1, _C_HEADER_3, 0.5) + (255,)
        # 1) 顶部挂环（小白矩形）
        _ild.rounded_rectangle([cx - 1.5 * 4, cy_bell - 9 * 4, cx + 1.5 * 4, cy_bell - 6 * 4],
                               radius=4, fill=(255, 255, 255, 255))
        # 2) 铃身（上窄下宽梯形，6 点钟造型）
        _ild.polygon([
            (cx - 7 * 4, cy_bell - 6 * 4),
            (cx + 7 * 4, cy_bell - 6 * 4),
            (cx + 9 * 4, cy_bell + 3 * 4),
            (cx - 9 * 4, cy_bell + 3 * 4),
        ], fill=(255, 255, 255, 255))
        # 3) 铃舌（底部紫色小圆）
        _ild.ellipse([cx - 2 * 4, cy_bell + 3 * 4, cx + 2 * 4, cy_bell + 7 * 4], fill=bell_color)
        # 注意：RGBA 图层 paste 必须把自身 alpha 作为 mask（第三参），否则 alpha 被忽略，
        # 透明区域 (0,0,0,0) 的 RGB 黑色会被直接覆盖 → 图标周围出现黑色矩形。
        _icon_small = _icon_layer.resize((40, 40), Image.LANCZOS)
        img.paste(_icon_small, (icon_x, icon_y), _icon_small)

        # 标题 + 副标题
        f_title = _get_font(bold=True, size=18)
        f_sub = _get_font(size=11)
        title_text = "新线索提醒"
        n_str = f"{n_leads} 条线索" if n_leads > 1 else ""
        draw.text((icon_x + 56, y + 16), title_text, font=f_title, fill=(255, 255, 255))
        sub = f"{now_str}  ·  {n_str}" if n_str else now_str
        draw.text((icon_x + 56, y + 42), sub, font=f_sub, fill=(240, 236, 255))
        y += HEADER_H + 18

        # ---------- 每条线索卡片 ----------
        for lead, ch, qr_img in zip(leads, card_heights, qr_imgs):
            card_top = y
            card_bottom = y + ch
            # 卡片白色圆角底 + 细边框（AA 超采样：圆角弧线平滑无锯齿）
            _paste_rounded_aa(img, [PAD, card_top, W - PAD, card_bottom], 20, ss=3,
                              fill=(255, 255, 255), outline=_C_LINE, width=1)
            cy = card_top + 12

            # ===== 头像（v4.7.0：整体 4x 超采样 —— 圆形渐变 + 首字 anchor=mm 精确居中）=====
            # 原 v4.6.0 逐行按弦宽画圆已消除"方+圆"，但 1:1 圆边仍有阶梯锯齿；
            # 且首字用 (avatar_size-21)/2 近似估算 y 会让字形视觉偏下。
            # 现在把整块头像（渐变圆 + 白首字）画进 4x RGBA 图层再 LANCZOS 缩回：
            # 圆边平滑、文字按字形 bbox（anchor="mm"）真正水平垂直居中。
            avatar_size = 46
            avatar_x = PAD + 16
            ax, ay = avatar_x, cy
            _ass = 4
            _as = avatar_size * _ass
            _alayer = Image.new("RGBA", (_as, _as), (0, 0, 0, 0))
            _ald = ImageDraw.Draw(_alayer)
            import math as _math
            _R = avatar_size / 2.0
            for ly in range(_as):
                dy_c = (ly + 0.5) / _ass - _R          # 该行相对圆心的纵向偏移
                half = _math.sqrt(max(0.0, _R * _R - dy_c * dy_c)) * _ass
                x_l = int(round(_as / 2.0 - half))
                x_r = int(round(_as / 2.0 + half - 1))
                if x_r >= x_l:
                    t = ly / (_as - 1)
                    c = _lerp_color(_C_HEADER_1, _C_HEADER_2, t)
                    _ald.line([(x_l, ly), (x_r, ly)], fill=c + (255,))
            # 首字（白色 + 加粗，anchor="mm" 以字形 bbox 居中，不再用字号估算）
            f_avatar = _get_font(bold=True, size=21 * _ass)
            initial = lead["name"][0] if lead["name"] else "?"
            _ald.text((_as / 2.0, _as / 2.0), initial, font=f_avatar,
                      fill=(255, 255, 255, 255), anchor="mm")
            # 同 icon：RGBA paste 必须带自身 alpha 作 mask，否则透明区黑矩形覆盖卡片白底
            _avatar_small = _alayer.resize((avatar_size, avatar_size), Image.LANCZOS)
            img.paste(_avatar_small, (int(round(ax)), int(round(ay))), _avatar_small)

            # 姓名（粗体大字，单独一行）
            f_name = _get_font(bold=True, size=20)
            draw.text((avatar_x + 58, cy + 6), lead["name"], font=f_name, fill=_C_TEXT)

            # 类型徽章（右上角，AA 胶囊）
            f_badge = _get_font(size=11)
            btxt = lead["badge_txt"]
            bw = _text_w(draw, btxt, f_badge) + 18
            fg, bg = _BADGE_COLORS[lead["badge_cls"]]
            _paste_rounded_aa(img, [W - PAD - 16 - bw, cy + 4, W - PAD - 16, cy + 4 + 22],
                              11, ss=4, fill=bg)
            draw.text((W - PAD - 16 - bw + 9, cy + 4 + 4), btxt, font=f_badge, fill=fg)

            # 第二行（顶部 UI 组件内）：序号 chip + 来客时间
            # v4.7.0：时间字号 11 → 13（与下方联系方式 label 同级），颜色加深便于阅读
            row2_y = cy + 34
            f_meta = _get_font(size=13)
            mx = avatar_x + 58
            if lead["serial"]:
                f_s = _get_font(size=11)
                sw = _text_w(draw, lead["serial"], f_s) + 14
                _paste_rounded_aa(img, [mx, row2_y, mx + sw, row2_y + 22], 11, ss=4,
                                  fill=(245, 243, 255))
                draw.text((mx + 7, row2_y + 4), lead["serial"], font=f_s, fill=(124, 58, 237))
                mx += sw + 12
            if lead["time"]:
                draw.ellipse([mx, row2_y + 8, mx + 6, row2_y + 14], fill=(167, 139, 250))
                draw.text((mx + 12, row2_y + 2), lead["time"], font=f_meta, fill=(100, 116, 139))
            cy += 60 + 14

            # ===== 联系块（v4.4.0：二维码 inline 到联系方式里）=====
            block_x0, block_x1 = PAD + 14, W - PAD - 14
            rows = []
            if lead["phone"]:
                rows.append(("电话", lead["phone"], _C_PHONE))
            if lead["wechat"]:
                rows.append(("微信", lead["wechat"], _C_WECHAT))
            if not rows and lead["note"]:
                rows.append(("备注", lead["note"], _C_SUB))

            # 块高：标题行 30 + 每行 30 + qr 行（如果存在）96
            qr_row_h = 108 if qr_img else 0  # QR 96 + 行内上下间距
            block_h = 30 + max(len(rows), 1) * 30 + qr_row_h
            _paste_rounded_aa(img, [block_x0, cy, block_x1, cy + block_h], 16, ss=3,
                              fill=_C_BLOCK_BG)
            # 区块标题：紫竖条 + "联系方式"
            _rounded(draw, [block_x0 + 14, cy + 9, block_x0 + 17, cy + 9 + 12], radius=2,
                     fill=_lerp_color(_C_HEADER_1, _C_HEADER_3, 0.5))
            f_block_title = _get_font(size=11)
            draw.text((block_x0 + 26, cy + 8), "联系方式", font=f_block_title, fill=_C_LABEL)

            ry = cy + 30
            f_label = _get_font(size=13)
            f_val = _get_font(bold=True, size=17)
            for label, value, color in rows:
                # 色点图标
                dot_colors = {"电话": _C_PHONE, "微信": _C_WECHAT, "备注": _C_SUB}
                dc = dot_colors.get(label, _C_SUB)
                _paste_ellipse_aa(img, [block_x0 + 14, ry + 7, block_x0 + 24, ry + 17], ss=4, fill=dc)
                draw.text((block_x0 + 34, ry), label, font=f_label, fill=_C_LABEL)
                draw.text((block_x0 + 88, ry - 2), value, font=f_val, fill=color)
                ry += 30

            # ===== 二维码 inline 行（仅当存在）=====
            if qr_img:
                qr = qr_img.copy()
                qr.thumbnail((96, 96))
                qx, qy = block_x0 + 14, ry + 6
                # 白色圆角底 + 细边框（AA）
                _paste_rounded_aa(img, [qx - 3, qy - 3, qx + 99, qy + 99], 10, ss=4,
                                  fill=(255, 255, 255), outline=_C_LINE, width=1)
                img.paste(qr, (qx, qy))
                # 右侧文字统一左对齐（x = qx+112），纵向 3 行
                text_x = qx + 112
                # 行 1：紫色小竖条 + "二维码" 标题
                _rounded(draw, [text_x, ry + 8, text_x + 3, ry + 22], radius=2,
                         fill=_lerp_color(_C_HEADER_1, _C_HEADER_3, 0.5))
                f_lab1 = _get_font(bold=True, size=14)
                draw.text((text_x + 8, ry + 5), "二维码", font=f_lab1, fill=_C_LABEL)
                # 行 2：微信扫一扫
                f_qr_hint = _get_font(size=12)
                draw.text((text_x, ry + 34), "微信扫一扫", font=f_qr_hint, fill=_C_SUB)
                # 行 3：直接添加客户 {微信号}（微信号加粗亮绿）
                wechat_text = lead["wechat"] or "客户"
                f_qr_hint_b = _get_font(bold=True, size=12)
                label3 = "直接添加客户 "
                draw.text((text_x, ry + 54), label3, font=f_qr_hint, fill=_C_SUB)
                draw.text((text_x + _text_w(draw, label3, f_qr_hint), ry + 54),
                          wechat_text, font=f_qr_hint_b, fill=_C_WECHAT)
                ry += 108

            # 防止 rows 为空时 ry 不前进（视觉对齐）
            if not rows and qr_row_h == 0:
                ry += 30

            cy = cy + block_h + 14

            # ===== 备注 =====
            if lead["note"]:
                note_h = 46
                _paste_rounded_aa(img, [block_x0, cy, block_x1, cy + note_h], 10, ss=3,
                                  fill=_C_NOTE_BG)
                f_note = _get_font(size=13)
                draw.text((block_x0 + 14, cy + (note_h - 20) // 2 - 3), "备注", font=f_label, fill=_C_LABEL)
                draw.text((block_x0 + 66, cy + (note_h - 20) // 2 - 3), lead["note"][:120], font=f_note,
                          fill=(107, 114, 128))
                cy += note_h + 10

            y = card_bottom + 14

        # ---------- footer 深蓝圆角渐变条 + 关键数字高亮 ----------
        # v4.6.0：原实现先画直角渐变矩形、再 rounded_rectangle(fill=None) 只描边不填充，
        # 圆角实际不生效 → 呈现"像圆角又是方的"+ 顶部白色锯齿残留。改为在独立图层绘制
        # 渐变，用圆角 mask 裁切后贴回画布，得到真正的圆角渐变条。
        # v4.7.0：mask 升到 4x 分辨率绘制再 LANCZOS 缩回 → 圆角弧线平滑无阶梯锯齿。
        _fw = W - 2 * PAD
        _foot_layer = Image.new("RGB", (_fw, FOOTER_H))
        _fd = ImageDraw.Draw(_foot_layer)
        _vgradient(_fd, 0, 0, _fw, FOOTER_H, _C_FOOTER_1, _C_FOOTER_2)
        _mask_ss = 4
        _foot_mask = Image.new("L", (_fw * _mask_ss, FOOTER_H * _mask_ss), 0)
        _md = ImageDraw.Draw(_foot_mask)
        try:
            _md.rounded_rectangle([0, 0, _fw * _mask_ss - 1, FOOTER_H * _mask_ss - 1],
                                  radius=16 * _mask_ss, fill=255)
        except AttributeError:
            _md.rectangle([0, 0, _fw * _mask_ss - 1, FOOTER_H * _mask_ss - 1], fill=255)
        _foot_mask = _foot_mask.resize((_fw, FOOTER_H), Image.LANCZOS)
        img.paste(_foot_layer, (PAD, y), _foot_mask)
        # 绿色脉冲点（AA）
        _paste_ellipse_aa(img, [PAD + 16, y + (FOOTER_H - 8) // 2, PAD + 24, y + (FOOTER_H - 8) // 2 + 8],
                          ss=4, fill=(52, 211, 153))
        # footer 文字拆段绘制，关键数字亮绿
        f_footer = _get_font(size=13)
        f_footer_b = _get_font(bold=True, size=13)
        base_x = PAD + 36
        cur_x = base_x
        fy = y + (FOOTER_H - 20) // 2

        # 段顺序：请在【X 分钟】内回复：【1】/好的 = 受理，【0】/没空 = 跳过
        segments = [
            ("请在 ", _C_BG_BASE := (238, 238, 255), False),
            (f"{timeout_minutes} 分钟", _C_HILITE, True),
            (" 内回复：", (238, 238, 255), False),
            ("1", _C_HILITE, True),
            ("/好的 = 受理，", (238, 238, 255), False),
            ("0", _C_HILITE, True),
            ("/没空 = 跳过", (238, 238, 255), False),
        ]
        for txt, color, bold in segments:
            font = f_footer_b if bold else f_footer
            draw.text((cur_x, fy), txt, font=font, fill=color)
            cur_x += _text_w(draw, txt, font) + 0

        from io import BytesIO
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        logger.warning("Pillow 渲染失败: %s", e)
        return None
