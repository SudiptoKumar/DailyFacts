from __future__ import annotations

import html
import re
from config import CATEGORY_ICONS, SETTINGS
from content_ai import EnrichedContent
from dataset import Fact
from emoji_engine import subject_emojis


def esc(value: str) -> str:
    return html.escape(value or "", quote=False)


def _safe_tags(tags: list[str], f: Fact) -> list[str]:
    clean: list[str] = []
    for raw in tags:
        value = re.sub(r"[^A-Za-z0-9]+", "", raw.replace("#", "").strip())
        if not value or value.lower() in {"dailyfacts", "fact", "facts", "didyouknow"}:
            continue
        candidate = "#" + value[:35]
        if candidate.lower() not in {x.lower() for x in clean}:
            clean.append(candidate)
        if len(clean) == 2:
            return clean

    for fallback_value in (f.category, f.subcategory, f.title):
        value = re.sub(r"[^A-Za-z0-9]+", "", fallback_value or "")
        if not value or value.lower() in {"dailyfacts", "fact", "facts", "didyouknow"}:
            continue
        candidate = "#" + value[:35]
        if candidate.lower() not in {x.lower() for x in clean}:
            clean.append(candidate)
        if len(clean) == 2:
            break

    if len(clean) == 1:
        clean.append("#Knowledge")
    return clean[:2]


def _category_icon(f: Fact) -> str:
    return CATEGORY_ICONS.get(f.category, "📚")


def _metadata_line(f: Fact) -> str:
    """Return the category header used by Daily Facts public posts.

    The reference image is from Today in History, so its date line is
    intentionally not copied. Daily Facts is evergreen and uses category
    as the metadata/header line instead.
    """
    return f"{_category_icon(f)} {f.category}".strip()


def _footer_html(f: Fact, content: EnrichedContent) -> str:
    tags = " ".join(_safe_tags(content.hashtags, f))
    channel = html.escape(SETTINGS.channel_url, quote=True)
    return f'<a href="{channel}"><b>{esc(SETTINGS.bot_name)}</b></a> {tags}'.strip()


def _title_line(f: Fact, content: EnrichedContent) -> str:
    subjects = subject_emojis(f, max_items=1)
    prefix = f"{subjects[0]} " if subjects else ""
    return f"{prefix}<b>{esc(content.title.strip() or 'Did You Know?')}</b>".strip()


def _post_html(f: Fact, content: EnrichedContent) -> str:
    return "\n\n".join(
        [
            f"<b>{esc(_metadata_line(f))}</b>",
            _title_line(f, content),
            esc(content.fact),
            _footer_html(f, content),
        ]
    )


def build_rich_message(f: Fact, content: EnrichedContent) -> dict:
    return {"html": _post_html(f, content)}


def build_rich_message_with_photo(f: Fact, content: EnrichedContent) -> dict:
    return build_rich_message(f, content)


def format_fallback_caption(f: Fact, content: EnrichedContent) -> str:
    """Build the exact Daily Facts card structure for a Telegram photo caption."""
    fact_text = content.fact.strip()
    title = content.title.strip() or "Did You Know?"

    def assemble(title_value: str, fact_value: str) -> str:
        return "\n\n".join(
            [
                f"<b>{esc(_metadata_line(f))}</b>",
                _title_line(f, EnrichedContent(title=title_value, fact=fact_value, hashtags=content.hashtags, image_query=content.image_query)),
                esc(fact_value),
                _footer_html(f, content),
            ]
        )

    body = assemble(title, fact_text)
    if len(body) <= 1024:
        return body

    while len(body) > 1024 and len(fact_text) > 120:
        fact_text = fact_text[: max(100, len(fact_text) - 30)].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
        body = assemble(title, fact_text)

    while len(body) > 1024 and len(title) > 35:
        title = title[: max(30, len(title) - 10)].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
        body = assemble(title, fact_text)

    if len(body) <= 1024:
        return body

    fact_text = fact_text[:120].rstrip() + "…"
    title = title[:50].rstrip() + "…"
    return assemble(title, fact_text)
