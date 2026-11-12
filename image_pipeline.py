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
# Quote-card heights: the card grows with the fact — 800 (3:2) for short
# facts, then 1200 (1:1), 1500 (4:5), 1800 (2:3). Width is always 1200.
# The 800 tier (Karn, 2026-11-12) uses the compact geometry below; the
# taller tiers keep the original layout.
CARD_HEIGHTS = (800, 1200, 1500, 1800)
# Compact (800) tier geometry, from Karn's approved 1200x800 sample:
# tighter border and header, the fact centered in the middle zone.
COMPACT_TEXT_W = 1200 - 80 - 80
COMPACT_TEXT_TOP = 262
COMPACT_TEXT_MAX_H = (800 - 144 - 48) - COMPACT_TEXT_TOP
# A fact only earns the compact tier if it fits that zone at 56px or
# larger — below that it belongs in a taller tier at a readable size.
COMPACT_MIN_FONT = 56
# The quote card's specimen-label template (Karn's final design,
# 2026-11-12): cream paper, ink structure, one red accent, DM Serif
# Display fact type with Archivo label type.
PAPER = (247, 242, 231)
INK = (24, 22, 19)
TEXT_INK = (28, 25, 21)
MUTED = (110, 100, 86)
RED = (224, 26, 44)
HANDLE = "@FactsNewsroom"

_FONT_URLS = {
    "serif": "https://raw.githubusercontent.com/google/fonts/main/ofl/playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf",
    "sans": "https://raw.githubusercontent.com/google/fonts/main/ofl/plusjakartasans/PlusJakartaSans%5Bwght%5D.ttf",
    "display": "https://fonts.gstatic.com/s/dmserifdisplay/v17/-nFnOHM81r4j6k0gjAW3mujVU2B2K_c.ttf",
    "archivo": "https://fonts.gstatic.com/s/archivo/v25/k3k6o8UDI-1M0wlSV9XAw6lQkqWY8Q82sJaRE-NWIDdgffTT0zRp8A.ttf",
    "archivo_medium": "https://fonts.gstatic.com/s/archivo/v25/k3k6o8UDI-1M0wlSV9XAw6lQkqWY8Q82sJaRE-NWIDdgffTTBjNp8A.ttf",
    "archivo_black": "https://fonts.gstatic.com/s/archivo/v25/k3k6o8UDI-1M0wlSV9XAw6lQkqWY8Q82sJaRE-NWIDdgffTTnTRp8A.ttf",
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


def _dashed_rectangle(draw: ImageDraw.ImageDraw, box, fill, width: int = 3, dash: int = 18, gap: int = 12) -> None:
    x0, y0, x1, y1 = box
    x = x0
    while x < x1:
        nxt = min(x + dash, x1)
        draw.line((x, y0, nxt, y0), fill=fill, width=width)
        draw.line((x, y1, nxt, y1), fill=fill, width=width)
        x += dash + gap
    y = y0
    while y < y1:
        nxt = min(y + dash, y1)
        draw.line((x0, y, x0, nxt), fill=fill, width=width)
        draw.line((x1, y, x1, nxt), fill=fill, width=width)
        y += dash + gap


def _tracked_strip(text: str, font, fill, tracking: int) -> Image.Image:
    """Render tracked text on its own strip, cropped to the real pixel
    box, so callers can align it by measured height (e.g. centered on a
    badge's midline) instead of guessing from font metrics."""
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    w = int(_tracked_width(probe, text, font, tracking)) + 8
    strip = Image.new("RGBA", (w, font.size * 2), (0, 0, 0, 0))
    _draw_tracked(ImageDraw.Draw(strip), 4, font.size // 2, text, font, fill, tracking)
    bbox = strip.getbbox()
    return strip.crop(bbox) if bbox else strip


def _fits_compact(quote: str) -> bool:
    """True when the fact fits the compact (800) tier's text zone at a
    readable size — the same auto-fit the renderer runs, against the
    compact zone, with COMPACT_MIN_FONT as the floor."""
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    for candidate in range(88, COMPACT_MIN_FONT - 1, -2):
        lines = _wrap(probe, quote, _font("display", candidate), COMPACT_TEXT_W)
        if len(lines) * candidate * 1.24 <= COMPACT_TEXT_MAX_H:
            return True
    return False


def _card_height(quote: str) -> int:
    """Content-fit card height: short facts take the compact 800 tier
    when they fit it at a readable size; otherwise the wrapped line
    count at a 48px reference size picks the tier — <=4 lines stay 1:1,
    5-7 lines go 4:5, longer facts go 2:3."""
    if _fits_compact(quote):
        return CARD_HEIGHTS[0]
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lines = len(_wrap(probe, quote, _font("display", 48), 1200 - 92 - 92))
    if lines <= 4:
        return CARD_HEIGHTS[1]
    if lines <= 7:
        return CARD_HEIGHTS[2]
    return CARD_HEIGHTS[3]


def generate_fallback_card(fact, content, output: Path) -> Path:
    """Quote card for facts with no photo, in the specimen-label template
    (Karn's final design, 2026-11-12): a cream label with a dashed ink
    border — the DF logo in a white badge beside the channel name, the
    category alone in red, the fact itself in DM Serif Display, and a
    source + handle footer on one baseline. The post is the card alone,
    with no extra text around it."""
    W = FRAME[0]
    quote = str(getattr(content, "fact", "") or getattr(fact, "fact", "") or "").strip()
    category = str(getattr(fact, "category", "") or "Facts").strip()
    domain = str(getattr(fact, "source_domain", "") or "").strip()
    H = _card_height(quote)

    # Per-tier geometry: the compact (800) tier tightens every spacing
    # (Karn's approved sample); taller tiers keep the original layout.
    compact = H == CARD_HEIGHTS[0]
    inset = 36 if compact else 44
    badge_x, badge_y, logo_h = (72, 56, 54) if compact else (84, 72, 60)
    badge_pad = 12 if compact else 14
    mx = 80 if compact else 92

    img = Image.new("RGB", (W, H), PAPER)
    draw = ImageDraw.Draw(img)

    # Dashed label border.
    _dashed_rectangle(draw, (inset, inset, W - inset, H - inset), INK)

    # Header: the DF logo in a white badge; the channel name centered on
    # the badge's midline by its measured pixel box.
    logo = None
    logo_path = Path(__file__).resolve().parent / "assets" / "df_logo.png"
    try:
        logo = Image.open(logo_path).convert("RGBA")
        logo = logo.resize((round(logo.width * logo_h / logo.height), logo_h), Image.Resampling.LANCZOS)
    except Exception:
        logo = None
    badge_w = (logo.width if logo is not None else (86 if compact else 97)) + badge_pad * 2
    badge_h = logo_h + badge_pad * 2
    badge = Image.new("RGBA", (badge_w, badge_h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(badge)
    bd.rounded_rectangle((1, 1, badge_w - 2, badge_h - 2), radius=18 if compact else 20, fill=(255, 255, 255), outline=INK, width=3)
    if logo is not None:
        badge.alpha_composite(logo, (badge_pad, badge_pad))
    else:
        df = _font("archivo_black", 30 if compact else 34)
        db = bd.textbbox((0, 0), "DF", font=df)
        bd.text(((badge_w - (db[2] - db[0])) / 2 - db[0], (badge_h - (db[3] - db[1])) / 2 - db[1]), "DF", font=df, fill=RED)
    base = img.convert("RGBA")
    base.alpha_composite(badge, (badge_x, badge_y))
    name = _tracked_strip("DAILY FACTS", _font("archivo_black", 42 if compact else 46), INK, 9)
    base.alpha_composite(name, (badge_x + badge_w + (38 if compact else 42), badge_y + badge_h // 2 - name.height // 2))
    img = base.convert("RGB")
    draw = ImageDraw.Draw(img)

    # Header rule, then the category alone in red.
    draw.line((mx, 168 if compact else 200, W - mx, 168 if compact else 200), fill=INK, width=3)
    _draw_tracked(draw, mx, 196 if compact else 238, category, _font("archivo", 30 if compact else 32), RED, 5)

    # Fact text: auto-fit the largest DM Serif size that fits the open
    # zone between the category line and the footer. Nothing else is
    # printed — the category sits above, the source in the footer, and
    # any extra line would only repeat the fact.
    rule_y = H - 144 if compact else H - 214
    text_x, text_w, text_top = mx, W - mx - mx, COMPACT_TEXT_TOP if compact else 292
    text_max_h = (rule_y - 48) - text_top
    size, lines = 28, []
    for candidate in range(88, 27, -2):
        font = _font("display", candidate)
        lines = _wrap(draw, quote, font, text_w)
        if len(lines) * candidate * 1.24 <= text_max_h:
            size = candidate
            break
    else:
        font = _font("display", 28)
        trimmed = quote
        while trimmed and len(_wrap(draw, trimmed, font, text_w)) * 28 * 1.24 > text_max_h:
            trimmed = trimmed[: trimmed.rfind(" ", 0, len(trimmed) - 8)].rstrip(" ,.;") if " " in trimmed else ""
        lines = _wrap(draw, (trimmed + "…") if trimmed else quote[:80] + "…", font, text_w)
        size = 28
    font = _font("display", size)
    y = text_top
    if compact:
        # The compact tier centers the fact in its middle zone (sample).
        y += (text_max_h - len(lines) * size * 1.24) / 2
    for line in lines:
        draw.text((text_x, y), line, font=font, fill=TEXT_INK)
        y += size * 1.24

    # Footer: ink rule, source left and handle right on one baseline.
    draw.line((mx, rule_y, W - mx, rule_y), fill=INK, width=3)
    baseline = rule_y + (62 if compact else 74)
    hf = _font("archivo_black", 34 if compact else 38)
    draw.text((W - mx - draw.textlength(HANDLE, font=hf), baseline - hf.getmetrics()[0]), HANDLE, font=hf, fill=INK)
    if domain:
        sf = _font("archivo_medium", 28 if compact else 30)
        draw.text((mx, baseline - sf.getmetrics()[0]), f"Source: {domain}", font=sf, fill=MUTED)
    return _save_jpeg(img, Path(output))
