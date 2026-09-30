from __future__ import annotations

import html
import re

from content_ai import EnrichedContent
from dataset import Fact
from emoji_engine import category_emoji, subject_emoji


def _escape_html_text(value: str) -> str:
    """Normalize any pre-escaped entities, then escape exactly once for Telegram HTML."""
    normalized = html.unescape(str(value or "")).strip()
    return html.escape(normalized, quote=False)


def _safe_tag(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_]", "", str(value).lstrip("#"))
    return value[:40] or "Facts"


def format_fallback_caption(fact: Fact, content: EnrichedContent) -> str:
    # Normalize plain text first, then HTML-escape exactly once.
    raw_category = fact.category.strip() or "Facts"
    cat = _escape_html_text(raw_category.upper())
    title = _escape_html_text(content.title.strip() or fact.title.strip() or "Did You Know?")
    body = _escape_html_text(content.fact.strip() or fact.fact.strip())
    tags = [_safe_tag(x) for x in content.hashtags[:2]]
    while len(tags) < 2:
        tags.append("Facts")
    caption = (
        f"{category_emoji(fact.category)} <b>{cat}</b>\n\n"
        f"{subject_emoji(content.title, content.fact, fact.category)} <b>{title}</b>\n\n"
        f"{body}\n\n"
        f"<a href=\"https://t.me/FactsNewsroom\"><b>Daily Facts</b></a> #{tags[0]} #{tags[1]}"
    )
    # Telegram photo captions have a 1024-character limit. Preserve the core fact rather than cutting HTML blindly.
    if len(caption) <= 1024:
        return caption
    body_limit = max(120, 1024 - (len(caption) - len(body)) - 3)
    trimmed = body[:body_limit].rstrip(" .") + "..."
    caption = caption.replace(body, trimmed, 1)
    return caption[:1024]
