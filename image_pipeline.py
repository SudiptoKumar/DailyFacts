from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

from config import GENERATED_DIR
from dataset import Fact
from image_resolver import ResolvedImage, resolve_fact_image

MAX_BYTES = 10 * 1024 * 1024
MAX_DIM_SUM = 10_000
MAX_DIM = 10_000


def _output_path(f: Fact) -> Path:
    return GENERATED_DIR / f"{f.fact_id}.jpg"


def _save_jpeg(image: Image.Image, output: Path, quality: int) -> None:
    image.save(output, "JPEG", quality=quality, optimize=True, progressive=True)


def _prepare_without_crop(source: Path, output: Path) -> Path:
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        width, height = image.size
        scale = min(1.0, MAX_DIM_SUM / max(1, width + height), MAX_DIM / max(1, width), MAX_DIM / max(1, height))
        if scale < 1.0:
            image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.Resampling.LANCZOS)
        quality = 95
        _save_jpeg(image, output, quality)
        while output.stat().st_size > MAX_BYTES and quality > 70:
            quality -= 5
            _save_jpeg(image, output, quality)
        if output.stat().st_size > MAX_BYTES:
            current = image
            for _ in range(6):
                new_size = (max(1, int(current.width * 0.88)), max(1, int(current.height * 0.88)))
                current = current.resize(new_size, Image.Resampling.LANCZOS)
                _save_jpeg(current, output, 85)
                if output.stat().st_size <= MAX_BYTES:
                    break
    return output


def prepare_fact_image(f: Fact, resolved: ResolvedImage | None = None) -> Path | None:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    result = resolved or resolve_fact_image(f)
    if not result:
        return None
    output = _output_path(f)
    return _prepare_without_crop(result.path, output)
