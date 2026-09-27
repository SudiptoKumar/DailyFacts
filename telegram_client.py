from __future__ import annotations

import json
import logging
from pathlib import Path

import requests

from config import SETTINGS

logger = logging.getLogger("daily-facts.telegram")


def _url(method: str) -> str:
    return f"https://api.telegram.org/bot{SETTINGS.telegram_bot_token}/{method}"


def _post(method: str, *, data=None, files=None) -> dict:
    if not SETTINGS.telegram_bot_token.strip():
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    if not SETTINGS.telegram_channel.strip():
        raise RuntimeError("TELEGRAM_CHANNEL is not configured.")
    last = None
    for attempt in range(1, 4):
        try:
            response = requests.post(_url(method), data=data, files=files, timeout=60)
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise RuntimeError(payload.get("description", "Telegram API error"))
            return payload["result"]
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last = exc
            logger.warning("Telegram %s attempt %d failed: %s", method, attempt, exc)
    raise RuntimeError(f"Telegram {method} failed: {last}")


def send_rich_message(rich_message_html: str, photo_path: str | None = None) -> int:
    """Send the Daily Facts post through Telegram's current Rich Messages API.

    When a photo is present, the HTML references tg://photo?id=cover and the
    same request binds that ID to a multipart InputMediaPhoto upload.
    """
    rich_payload: dict = {
        "html": rich_message_html,
        "skip_entity_detection": False,
    }
    file_handle = None
    files = None
    if photo_path:
        path = Path(photo_path)
        if not path.exists():
            raise FileNotFoundError(path)
        rich_payload["media"] = [
            {
                "id": "cover",
                "media": {"type": "photo", "media": f"attach://{path.name}"},
            }
        ]
        file_handle = path.open("rb")
        files = {path.name: (path.name, file_handle, "image/jpeg")}

    try:
        result = _post(
            "sendRichMessage",
            data={
                "chat_id": SETTINGS.telegram_channel,
                "rich_message": json.dumps(rich_payload, ensure_ascii=False),
            },
            files=files,
        )
        return int(result["message_id"])
    finally:
        if file_handle is not None:
            file_handle.close()


def send_photo(path: str, caption: str) -> int:
    with open(path, "rb") as handle:
        result = _post(
            "sendPhoto",
            data={
                "chat_id": SETTINGS.telegram_channel,
                "caption": caption,
                "parse_mode": "HTML",
            },
            files={"photo": handle},
        )
    return int(result["message_id"])


def send_message(text: str) -> int:
    result = _post(
        "sendMessage",
        data={
            "chat_id": SETTINGS.telegram_channel,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        },
    )
    return int(result["message_id"])
