from __future__ import annotations

import html
import re

from config import CATEGORY_ICONS, SETTINGS
from content_ai import EnrichedContent
from dataset import Fact


def esc(value: str) -> str:
    return html.escape(value or "", quote=False)


def category_line(f: Fact) -> str:
    return "🧠 DID YOU KNOW?"


def source_display_name(f: Fact) -> str:
    return f.source_title.strip() or f.source_domain.strip() or "Source"


def _safe_tags(tags: list[str], f: Fact) -> list[str]:
    clean: list[str] = []
    for raw in tags:
        value = re.sub(r"[^A-Za-z0-9]+", "", raw.replace("#", "").strip())
        if not value:
            continue
        candidate = "#" + value[:35]
        if candidate.lower() not in {x.lower() for x in clean}:
            clean.append(candidate)
    # Always expose Daily Facts as the stable channel tag.
    clean = [x for x in clean if x.lower() != "#dailyfacts"][:2]
    clean.append("#DailyFacts")
    return clean[:3]


def build_rich_message(f: Fact, content: EnrichedContent) -> dict:
    source_name = esc(source_display_name(f))
    source_url = html.escape(f.source_url, quote=True)
    blocks = [
        {"type": "paragraph", "text": [{"type": "bold", "text": esc(category_line(f))}]},
        {"type": "heading", "size": 3, "text": esc(content.title)},
        {"type": "paragraph", "text": esc(content.fact)},
        {"type": "paragraph", "text": [{"type": "bold", "text": "💡 Why it's interesting"}]},
        {"type": "paragraph", "text": esc(content.interesting)},
        {"type": "paragraph", "text": " ".join(_safe_tags(content.hashtags, f))},
        {"type": "paragraph", "text": ["🔗 ", {"type": "url", "text": source_name, "url": f.source_url}]},
    ]
    return {"blocks": blocks}


def build_rich_message_with_photo(f: Fact, content: EnrichedContent) -> dict:
    payload = build_rich_message(f, content)
    payload["blocks"].insert(0, {"type": "photo", "photo": {"type": "photo", "media": "attach://photo"}})
    return payload


def format_fallback_caption(f: Fact, content: EnrichedContent) -> str:
    tags = " ".join(_safe_tags(content.hashtags, f))
    source_url = html.escape(f.source_url, quote=True)
    source_name = esc(source_display_name(f))

    def assemble(fact_text: str, interesting_text: str) -> str:
        return "\n\n".join([
            f"<b>{esc(category_line(f))}</b>",
            f"<b>{esc(content.title)}</b>",
            esc(fact_text),
            "<b>💡 Why it's interesting</b>",
            esc(interesting_text),
            tags,
            f'🔗 <a href="{source_url}">{source_name}</a>',
        ])

    fact_text = content.fact.strip()
    interesting = content.interesting.strip()
    body = assemble(fact_text, interesting)
    if len(body) <= 1024:
        return body

    # Preserve valid HTML by shortening plain-text fields before final assembly.
    while len(body) > 1024 and len(interesting) > 40:
        interesting = interesting[:max(20, len(interesting) - 20)].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
        body = assemble(fact_text, interesting)

    while len(body) > 1024 and len(fact_text) > 80:
        fact_text = fact_text[:max(60, len(fact_text) - 30)].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
        body = assemble(fact_text, interesting)

    # This should only be reachable with an unusually long source title/URL.
    if len(body) <= 1024:
        return body
    return assemble(fact_text[:80].rstrip() + "…", interesting[:80].rstrip() + "…")[:1024]
