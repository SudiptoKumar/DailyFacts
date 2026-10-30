from __future__ import annotations

import os
import tempfile
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

MAX_DIMENSION = 1536
MAX_BYTES = 9_500_000

# Every published photo leaves at a fixed width of 1200 px with its original
# aspect ratio kept (nothing cropped), stamped. The generated quote card
# keeps the fixed 1200x675 (16:9) frame.
FRAME = (1200, 1200)
# Quote-card heights (Karn, 2026-10-30): the card is never shorter than
# square — 1:1 minimum — and grows taller with the fact: 1200 (1:1),
# 1500 (4:5), 1800 (2:3). Width is always 1200.
CARD_HEIGHTS = (1200, 1500, 1800)
# The network's house template (Vocabulary/Sports cards): warm paper, ink,
# one red accent, hairline frame, Playfair display type.
PAPER = (252, 251, 248)
INK = (23, 23, 23)
MUTED = (108, 104, 99)
RED = (227, 27, 46)
HAIRLINE = (238, 234, 228)
HANDLE = "@FactsNewsroom"

_FONT_URLS = {
    "serif": "https://raw.githubusercontent.com/google/fonts/main/ofl/playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf",
    "sans": "https://raw.githubusercontent.com/google/fonts/main/ofl/plusjakartasans/PlusJakartaSans%5Bwght%5D.ttf",
}
_FONT_FILES: dict[str, str] = {}


def _font_file(family: str) -> str:
    """Download the display font once per process (same approach as the
    Sports bot); return "" when unavailable so callers can fall back."""
    if family in _FONT_FILES:
        return _FONT_FILES[family]
    path = ""
    url = _FONT_URLS.get(family)
    if url:
        target = os.path.join(tempfile.gettempdir(), f"facts_font_{family}.ttf")
        try:
            if os.path.isfile(target) and os.path.getsize(target) > 10000:
                path = target
            else:
                response = requests.get(url, timeout=30, headers={"User-Agent": "DailyFacts/1.0"})
                response.raise_for_status()
                with open(target, "wb") as fh:
                    fh.write(response.content)
                if os.path.getsize(target) > 10000:
                    path = target
        except Exception:
            path = ""
    _FONT_FILES[family] = path
    return path


def _font(family: str, size: int, weight: int = 700):
    path = _font_file(family)
    if path:
        try:
            font = ImageFont.truetype(path, size=size)
            try:
                font.set_variation_by_axes([weight])
            except Exception:
                pass
            return font
        except Exception:
            pass
    candidate = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    if os.path.isfile(candidate):
        try:
            return ImageFont.truetype(candidate, size=size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def _fit_width(img: Image.Image) -> Image.Image:
    """Resize to the fixed publish width, keeping the original aspect
    ratio — the full photo stays visible, nothing is cropped."""
    W = FRAME[0]
    scale = W / img.width
    return img.resize((W, max(1, round(img.height * scale))), Image.Resampling.LANCZOS)


def _stamp(rgba: Image.Image) -> Image.Image:
    """Adaptive handle chip, bottom-right (the network's photo treatment):
    light chip on dark images, dark chip on light images."""
    W, H = rgba.size
    brightness = rgba.convert("L").resize((1, 1)).getpixel((0, 0))
    if brightness < 125:
        bg, fg = (245, 245, 245, 225), (20, 24, 28, 255)
    else:
        bg, fg = (18, 22, 28, 205), (245, 245, 245, 255)
    overlay = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _font("sans", 24, 700)
    bbox = draw.textbbox((0, 0), HANDLE, font=font)
    chip_w = (bbox[2] - bbox[0]) + 44
    chip_h = (bbox[3] - bbox[1]) + 20
    x2, y2 = W - 28, H - 24
    draw.rounded_rectangle((x2 - chip_w, y2 - chip_h, x2, y2), radius=18, fill=bg)
    draw.text((x2 - chip_w + 22, y2 - chip_h + 10 - bbox[1]), HANDLE, font=font, fill=fg)
    return Image.alpha_composite(rgba, overlay)


def _save_jpeg(img: Image.Image, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    quality = 90
    while quality >= 70:
        img.save(output, format="JPEG", quality=quality, optimize=True)
        if output.stat().st_size <= MAX_BYTES:
            return output
        quality -= 5
    return output


def _prepare_image(source: Path, output: Path) -> Path:
    with Image.open(source) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        framed = _stamp(_fit_width(img).convert("RGBA")).convert("RGB")
        return _save_jpeg(framed, output)


def _prepare_without_crop(source: Path, output: Path) -> Path:
    return _prepare_image(source, output)


def prepare_fact_image(fact, resolved):
    source = Path(resolved.local_path)
    output = Path(resolved.output_path)
    return _prepare_image(source, output)


def _tracked_width(draw: ImageDraw.ImageDraw, s: str, font, tracking: int) -> float:
    return sum(draw.textlength(ch, font=font) + tracking for ch in s) - (tracking if s else 0)


def _draw_tracked(draw: ImageDraw.ImageDraw, x: float, y: float, s: str, font, fill, tracking: int) -> float:
    for ch in s:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking
    return x


def _wrap(draw: ImageDraw.ImageDraw, s: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in s.split():
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _card_height(quote: str) -> int:
    """Content-fit card height: the wrapped line count at a 48px reference
    size picks the tier — <=4 lines stay 1:1, 5-7 lines go 4:5, longer
    facts go 2:3. The card is never shorter than square (Karn's rule)."""
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lines = len(_wrap(probe, quote, _font("serif", 48, 700), 1200 - 140 - 84))
    if lines <= 4:
        return CARD_HEIGHTS[0]
    if lines <= 7:
        return CARD_HEIGHTS[1]
    return CARD_HEIGHTS[2]


def generate_fallback_card(fact, content, output: Path) -> Path:
    """Quote card for facts with no photo, in the network's house template
    (paper, ink, Playfair, red accent, hairline frame): the channel header
    with the DF logo, then the fact itself in big serif type with a red
    quote mark and rule, and a handle + source footer — nothing else. The
    post is the card alone, with no extra text around it."""
    W = FRAME[0]
    quote = str(getattr(content, "fact", "") or getattr(fact, "fact", "") or "").strip()
    category = str(getattr(fact, "category", "") or "Facts").strip()
    domain = str(getattr(fact, "source_domain", "") or "").strip()
    H = _card_height(quote)

    img = Image.new("RGB", (W, H), PAPER)
    draw = ImageDraw.Draw(img)

    # Hairline frame.
    draw.rounded_rectangle((24, 24, W - 24, H - 24), radius=24, outline=HAIRLINE, width=2)

    # Header: the channel's original DF logo + DAILY FACTS // CATEGORY.
    mx, my = 92, 56
    mark_w = 46
    logo_path = Path(__file__).resolve().parent / "assets" / "df_logo.png"
    try:
        logo = Image.open(logo_path).convert("RGBA")
        logo_h = 54
        logo = logo.resize((round(logo.width * logo_h / logo.height), logo_h), Image.Resampling.LANCZOS)
        base = img.convert("RGBA")
        base.alpha_composite(logo, (mx, my - 4))
        img = base.convert("RGB")
        draw = ImageDraw.Draw(img)
        mark_w = logo.width
    except Exception:
        draw.rounded_rectangle((mx, my, mx + mark_w, my + mark_w), radius=10, fill=RED)
        qf = _font("sans", 30, 800)
        qb = draw.textbbox((0, 0), "?", font=qf)
        draw.text((mx + (mark_w - (qb[2] - qb[0])) / 2 - qb[0], my + (mark_w - (qb[3] - qb[1])) / 2 - qb[1]), "?", font=qf, fill=(255, 255, 255))
    hf = _font("sans", 33, 800)
    x = _draw_tracked(draw, mx + mark_w + 22, my + 4, "DAILY FACTS", hf, INK, 2)
    gf = _font("sans", 30, 600)
    draw.text((x + 18, my + 6), "//", font=gf, fill=MUTED)
    _draw_tracked(draw, x + 62, my + 6, category.upper(), gf, MUTED, 2)

    # Red quote mark.
    draw.text((86, 128), "“", font=_font("serif", 130, 800), fill=RED)

    # Fact text: auto-fit the largest serif size, then center the block in
    # the open zone between the quote mark and the footer. Nothing else is
    # printed — the category is in the header, the source in the footer,
    # and any extra line would only repeat the fact.
    text_x, text_w = 140, W - 140 - 84
    zone_top, zone_bottom = 235, H - 118
    text_max_h = zone_bottom - zone_top
    size, lines = 28, []
    for candidate in range(58, 27, -2):
        font = _font("serif", candidate, 700)
        lines = _wrap(draw, quote, font, text_w)
        if len(lines) * candidate * 1.24 <= text_max_h:
            size = candidate
            break
    else:
        font = _font("serif", 28, 700)
        trimmed = quote
        while trimmed and len(_wrap(draw, trimmed, font, text_w)) * 28 * 1.24 > text_max_h:
            trimmed = trimmed[: trimmed.rfind(" ", 0, len(trimmed) - 8)].rstrip(" ,.;") if " " in trimmed else ""
        lines = _wrap(draw, (trimmed + "…") if trimmed else quote[:80] + "…", font, text_w)
        size = 28
    font = _font("serif", size, 700)
    line_h = size * 1.24
    text_h = len(lines) * line_h
    text_top = zone_top + max(0, (text_max_h - text_h) / 2)
    y = text_top
    for line in lines:
        draw.text((text_x, y), line, font=font, fill=INK)
        y += line_h
    text_bottom = y - (line_h - size)
    draw.rounded_rectangle((92, text_top + 6, 99, text_bottom), radius=3, fill=RED)

    # Footer: hairline, handle left, source right.
    draw.line((92, H - 92, W - 92, H - 92), fill=HAIRLINE, width=2)
    draw.text((92, H - 66), HANDLE, font=_font("sans", 26, 800), fill=INK)
    if domain:
        sf = _font("sans", 24, 500)
        label = f"Source: {domain}"
        draw.text((W - 92 - draw.textlength(label, font=sf), H - 64), label, font=sf, fill=MUTED)
    return _save_jpeg(img, Path(output))
