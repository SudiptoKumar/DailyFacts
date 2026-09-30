from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, str(default)).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_channel: str = os.getenv("TELEGRAM_CHANNEL", "@FactsNewsroom")
    cerebras_api_key: str = os.getenv("CEREBRAS_API_KEY", "")
    cerebras_model: str = os.getenv("CEREBRAS_MODEL", "gpt-oss-120b")
    exa_api_key: str = os.getenv("EXA_API_KEY", "")
    use_cerebras: bool = _bool("USE_CEREBRAS", True)
    use_exa_context: bool = _bool("USE_EXA_CONTEXT", False)
    timezone: str = os.getenv("TIMEZONE", "Asia/Dhaka")
    posts_per_day: int = _int("POSTS_PER_DAY", 20)
    batch_size: int = _int("BATCH_SIZE", 10)
    catch_up_max: int = _int("CATCH_UP_MAX", 10)
    strict_dataset: bool = _bool("STRICT_DATASET", False)
    image_required: bool = _bool("IMAGE_REQUIRED", False)
    image_min_score: int = _int("IMAGE_MIN_SCORE", 16)
    cache_images: bool = _bool("CACHE_IMAGES", True)
    block_batch2_without_batch1_receipt: bool = _bool("BLOCK_BATCH2_WITHOUT_BATCH1_RECEIPT", True)
    request_timeout: int = _int("REQUEST_TIMEOUT", 20)
    image_timeout: int = _int("IMAGE_TIMEOUT", 25)


SETTINGS = Settings()
DATA_DIR = ROOT / "data"
STATE_DIR = ROOT / "state"
GENERATED_DIR = ROOT / "generated"
LOG_DIR = ROOT / "logs"
STATE_FILE = STATE_DIR / "publication_state.json"

for _path in (STATE_DIR, GENERATED_DIR, LOG_DIR):
    _path.mkdir(parents=True, exist_ok=True)
