from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageFilter, ImageOps, ImageStat

from config import GENERATED_DIR
from dataset import Fact
from image_resolver import ResolvedImage, resolve_fact_image

MAX_BYTES = 10 * 1024 * 1024
MAX_DIM_SUM = 10_000
MAX_DIM = 10_000
TARGET_WIDTH = 1536
TARGET_HEIGHT = 1024
TARGET_RATIO = TARGET_WIDTH / TARGET_HEIGHT


def _output_path(f: Fact) -> Path:
    return GENERATED_DIR / f"{f.fact_id}.jpg"


def _save_jpeg(image: Image.Image, output: Path, quality: int) -> None:
    image.save(output, "JPEG", quality=quality, optimize=True, progressive=True, subsampling=0)


def _edge_blur_background(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    bg = ImageOps.fit(image, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    bg = bg.filter(ImageFilter.GaussianBlur(radius=24))
    # Slightly reduce contrast without introducing a synthetic-looking solid frame.
    mean = ImageStat.Stat(bg).mean
    avg = sum(mean) / 3.0
    if avg > 190:
        overlay = Image.new("RGB", size, (35, 35, 35))
        bg = Image.blend(bg, overlay, 0.10)
    return bg


def _normalize_to_standard_ratio(image: Image.Image) -> Image.Image:
    """Return an exact 3:2 image while preserving the subject whenever possible.

    Near-3:2 photographs get a minimal smart crop. Extreme aspect ratios use a
    blurred continuation background so the complete source image remains visible.
    """
    image = image.convert("RGB")
    width, height = image.size
    if width < 2 or height < 2:
        raise ValueError("Image is too small")

    source_ratio = width / height
    if abs(source_ratio - TARGET_RATIO) < 0.08:
        return ImageOps.fit(
            image,
            (TARGET_WIDTH, TARGET_HEIGHT),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )

    # Preserve the full source for portrait/ultra-wide images.
    canvas = _edge_blur_background(image, (TARGET_WIDTH, TARGET_HEIGHT))
    contained = ImageOps.contain(
        image,
        (TARGET_WIDTH, TARGET_HEIGHT),
        method=Image.Resampling.LANCZOS,
    )
    x = (TARGET_WIDTH - contained.width) // 2
    y = (TARGET_HEIGHT - contained.height) // 2
    canvas.paste(contained, (x, y))
    return canvas


def _prepare_image(source: Path, output: Path) -> Path:
    with Image.open(source) as opened:
        opened.verify()
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")

    image = _normalize_to_standard_ratio(image)

    # 1536x1024 is already safely inside Telegram's photo dimension limits,
    # while matching the requested 3:2 presentation ratio.
    quality = 94
    _save_jpeg(image, output, quality)
    while output.stat().st_size > MAX_BYTES and quality > 70:
        quality -= 5
        _save_jpeg(image, output, quality)

    if output.stat().st_size > MAX_BYTES:
        current = image
        for _ in range(6):
            new_size = (max(1, int(current.width * 0.90)), max(1, int(current.height * 0.90)))
            current = current.resize(new_size, Image.Resampling.LANCZOS)
            _save_jpeg(current, output, 85)
            if output.stat().st_size <= MAX_BYTES:
                break
    return output


def _prepare_without_crop(source: Path, output: Path) -> Path:
    """Backward-compatible entry point used by cached-image recovery."""
    return _prepare_image(source, output)


def prepare_fact_image(f: Fact, resolved: ResolvedImage | None = None) -> Path | None:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    result = resolved or resolve_fact_image(f)
    if not result:
        return None
    output = _output_path(f)
    return _prepare_image(result.path, output)
