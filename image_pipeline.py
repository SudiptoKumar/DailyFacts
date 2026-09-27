from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

from config import GENERATED_DIR
from dataset import Fact
from image_resolver import ResolvedImage, resolve_fact_image

MAX_BYTES = 10 * 1024 * 1024
MAX_WIDTH = 1536
MAX_HEIGHT = 1024


def _output_path(f: Fact) -> Path:
    return GENERATED_DIR / f"{f.fact_id}.jpg"


def _save_jpeg(image: Image.Image, output: Path, quality: int) -> None:
    image.save(output, "JPEG", quality=quality, optimize=True, progressive=True, subsampling=0)


def _prepare_image(source: Path, output: Path) -> Path:
    """Prepare a Telegram photo without changing its aspect ratio or adding a background.

    The source composition is preserved. Only orientation, proportional downscaling,
    JPEG encoding and file-size reduction are applied. No crop, blur, padding or
    artificial color frame is ever introduced.
    """
    with Image.open(source) as opened:
        opened.verify()

    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")

    if image.width < 2 or image.height < 2:
        raise ValueError("Image is too small")

    # Proportionally downscale only when the source exceeds the standard Telegram
    # presentation envelope. Smaller images are kept at their native dimensions.
    if image.width > MAX_WIDTH or image.height > MAX_HEIGHT:
        image.thumbnail((MAX_WIDTH, MAX_HEIGHT), Image.Resampling.LANCZOS)

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

    # Final integrity check after all processing.
    with Image.open(output) as final_image:
        final_image.verify()

    return output


def _prepare_without_crop(source: Path, output: Path) -> Path:
    """Backward-compatible entry point. Despite the historical name, no crop occurs."""
    return _prepare_image(source, output)


def prepare_fact_image(f: Fact, resolved: ResolvedImage | None = None) -> Path | None:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    result = resolved or resolve_fact_image(f)
    if not result:
        return None
    output = _output_path(f)
    return _prepare_image(result.path, output)
