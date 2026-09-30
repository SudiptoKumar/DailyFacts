from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import requests

from config import SETTINGS

logger = logging.getLogger("daily-facts.telegram")


def _url(method: str) -> str:
    return f"https://api.telegram.org/bot{SETTINGS.telegram_bot_token}/{method}"


def _post(method: str, *, data=None, files=None) -> dict:
    """Send a Telegram request without unsafe blind retries.

    A network timeout/5xx can happen after Telegram has accepted a message. Retrying
    such a non-idempotent call can create duplicate channel posts. Only HTTP 429 is
    retried because Telegram explicitly rejected the request and supplies a delay.
    """
    if not SETTINGS.telegram_bot_token.strip():
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    if not SETTINGS.telegram_channel.strip():
        raise RuntimeError("TELEGRAM_CHANNEL is not configured.")

    try:
        response = requests.post(_url(method), data=data, files=files, timeout=60)
    except requests.RequestException as exc:
        raise RuntimeError(f"Telegram {method} transport failure: {exc}") from exc

    if response.status_code == 429:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        retry_after = int(((payload.get("parameters") or {}).get("retry_after") or 1))
        retry_after = max(1, min(retry_after, 30))
        logger.warning("Telegram %s rate limited; waiting %ss before one safe retry.", method, retry_after)
        time.sleep(retry_after)
        try:
            response = requests.post(_url(method), data=data, files=files, timeout=60)
        except requests.RequestException as exc:
            raise RuntimeError(f"Telegram {method} transport failure after 429 retry: {exc}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Telegram {method} returned non-JSON HTTP {response.status_code}") from exc

    if not response.ok or not payload.get("ok"):
        raise RuntimeError(payload.get("description", f"Telegram API HTTP {response.status_code}"))
    return payload["result"]


def send_rich_message(rich_message_html: str, photo_path: str | None = None) -> int:
    rich_payload: dict = {"html": rich_message_html, "skip_entity_detection": False}
    file_handle = None
    files = None
    if photo_path:
        path = Path(photo_path)
        if not path.exists():
            raise FileNotFoundError(path)
        rich_payload["media"] = [
            {"id": "cover", "media": {"type": "photo", "media": f"attach://{path.name}"}}
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
