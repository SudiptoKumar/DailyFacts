from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
GENERATED_DIR = BASE_DIR / "generated"
STATE_DIR = BASE_DIR / "state"
STATE_FILE = STATE_DIR / "posted_state.json"
LOG_DIR = BASE_DIR / "logs"

MONTHS = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December",
}
MONTH_NUMBERS = {name.lower(): num for num, name in MONTHS.items()}

CATEGORY_ICONS = {
    "Science": "🔬",
    "Space": "🌌",
    "Human Body": "🫀",
    "Psychology": "🧠",
    "Animals": "🐾",
    "Geography": "🌍",
    "History": "🏛️",
    "Technology": "💻",
    "Mystery & Unexplained": "🔎",
    "Ancient Civilizations": "🏺",
    "World Records & Extremes": "🏆",
    "Nature": "🌿",
    "Food": "🍽️",
    "Inventions": "💡",
    "Country Facts": "🌐",
    "Mythology & Legends": "📜",
    "Ocean & Marine": "🌊",
    "Language": "🔤",
    "Money & Economics": "💰",
    "Everyday Life": "🏠",
}

KNOWN_CATEGORIES = frozenset(CATEGORY_ICONS)


def normalize_telegram_channel(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    for prefix in (
        "https://t.me/", "http://t.me/", "https://telegram.me/", "http://telegram.me/",
        "t.me/", "telegram.me/",
    ):
        if value.lower().startswith(prefix):
            value = value[len(prefix):].split("/", 1)[0].strip()
            break
    if value and not value.startswith("@") and not value.startswith("-") and not value.isdigit():
        value = "@" + value
    return value


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}")


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_channel: str = normalize_telegram_channel(os.getenv("TELEGRAM_CHANNEL", "@FactsNewsroom"))
    timezone: str = os.getenv("TIMEZONE", "Asia/Dhaka")
    posts_per_day: int = _env_int("POSTS_PER_DAY", 20)
    use_cerebras: bool = _env_bool("USE_CEREBRAS", True)
    use_exa_context: bool = _env_bool("USE_EXA_CONTEXT", False)
    cerebras_model: str = os.getenv("CEREBRAS_MODEL", "gpt-oss-120b")
    cerebras_api_key: str = os.getenv("CEREBRAS_API_KEY", "")
    exa_api_key: str = os.getenv("EXA_API_KEY", "")
    strict_dataset: bool = _env_bool("STRICT_DATASET", False)
    image_required: bool = _env_bool("IMAGE_REQUIRED", False)
    image_min_score: int = _env_int("IMAGE_MIN_SCORE", 8)
    max_image_candidates: int = _env_int("MAX_IMAGE_CANDIDATES", 20)
    cache_images: bool = _env_bool("CACHE_IMAGES", True)
    catch_up_max: int = _env_int("CATCH_UP_MAX", 10)
    request_timeout: int = _env_int("REQUEST_TIMEOUT", 25)
    bot_name: str = os.getenv("BOT_NAME", "Daily Facts")
    channel_url: str = os.getenv("CHANNEL_URL", "https://t.me/FactsNewsroom")


SETTINGS = Settings()

REQUIRED_COLUMNS = {
    "ID", "Day", "Date", "Fact", "Slot", "Month", "Title", "Category",
    "Evidence", "Confidence", "Source URL", "Subcategory", "Source title",
    "Source domain", "Verification status",
}


def canonical_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return value
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}" + (f"?{parsed.query}" if parsed.query else "")
