from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

MAX_DIMENSION = 1536
MAX_BYTES = 9_500_000


def _prepare_image(source: Path, output: Path) -> Path:
    with Image.open(source) as img:
        img = ImageOps.exif_transpose(img)
        width, height = img.size
        scale = min(1.0, MAX_DIMENSION / max(width, height))
        if scale < 1.0:
            img = img.resize((max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.LANCZOS)
        if img.mode in {"RGBA", "LA", "P"}:
            rgba = img.convert("RGBA")
            canvas = Image.new("RGB", rgba.size, "white")
            canvas.paste(rgba, mask=rgba.getchannel("A"))
            img = canvas
        else:
            img = img.convert("RGB")
        output.parent.mkdir(parents=True, exist_ok=True)
        quality = 90
        while quality >= 70:
            img.save(output, format="JPEG", quality=quality, optimize=True)
            if output.stat().st_size <= MAX_BYTES:
                return output
            quality -= 5
        return output


def _prepare_without_crop(source: Path, output: Path) -> Path:
    return _prepare_image(source, output)


def prepare_fact_image(fact, resolved):
    source = Path(resolved.local_path)
    output = Path(resolved.output_path)
    return _prepare_image(source, output)
