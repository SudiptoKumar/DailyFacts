from __future__ import annotations

import html
import re

from content_ai import EnrichedContent
from dataset import Fact
from emoji_engine import category_line, context_label, subject_emojis
from config import SETTINGS


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
    if len(clean) < 2:
        for fallback in ("#" + re.sub(r"[^A-Za-z0-9]+", "", f.category)[:35], "#" + re.sub(r"[^A-Za-z0-9]+", "", f.subcategory)[:35]):
            if fallback != "#" and fallback.lower() not in {x.lower() for x in clean}:
                clean.append(fallback)
            if len(clean) >= 2:
                break
    return clean[:2]


def _footer_text(f: Fact, content: EnrichedContent) -> str:
    return " ".join(["𝗗𝗮𝗶𝗹𝘆 𝗙𝗮𝗰𝘁𝘀", *_safe_tags(content.hashtags, f)])


def _footer_html(f: Fact, content: EnrichedContent) -> str:
    tags = " ".join(_safe_tags(content.hashtags, f))
    return f'<a href="{html.escape(SETTINGS.channel_url, quote=True)}"><b>Daily Facts</b></a> {tags}'.strip()


def _title_line(f: Fact, content: EnrichedContent) -> str:
    subject = " ".join(subject_emojis(f))
    title = content.title.strip() or "Did You Know?"
    return f"{subject} {title}".strip()


def _post_html(f: Fact, content: EnrichedContent) -> str:
    title = content.title.strip() or "Did You Know?"
    subject = " ".join(subject_emojis(f)).strip()
    title_line = f"{esc(subject)} <b>{esc(title)}</b>".strip() if subject else f"<b>{esc(title)}</b>"
    footer = _footer_html(f, content)
    return "\n\n".join([
        f"<b>{esc(category_line(f))}</b>",
        title_line,
        esc(content.fact),
        f"<b>{esc(context_label(f))}</b>",
        esc(content.interesting),
        footer,
    ])


def build_rich_message(f: Fact, content: EnrichedContent) -> dict:
    # Bot API 10.1+ documents InputRichMessage as exactly one of html, markdown,
    # or blocks. HTML is the most robust representation for this compact post.
    return {"html": _post_html(f, content)}


def build_rich_message_with_photo(f: Fact, content: EnrichedContent) -> dict:
    # Kept for API compatibility with callers/tests. Local photos are now sent
    # using sendPhoto with the same HTML caption instead of attach://photo inside
    # a rich-message block.
    return build_rich_message(f, content)



def format_fallback_caption(f: Fact, content: EnrichedContent) -> str:
    footer = _footer_html(f, content)
    title_line = _title_line(f, content)
    context = context_label(f)

    def assemble(fact_text: str, interesting_text: str) -> str:
        return "\n\n".join([
            f"<b>{esc(category_line(f))}</b>",
            f"{esc(' '.join(subject_emojis(f)))} <b>{esc(content.title.strip() or 'Did You Know?')}</b>".strip(),
            esc(fact_text),
            f"<b>{esc(context)}</b>",
            esc(interesting_text),
            footer,
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

    if len(body) <= 1024:
        return body
    return assemble(fact_text[:80].rstrip() + "…", interesting[:80].rstrip() + "…")[:1024]
