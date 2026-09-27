from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import requests

from config import SETTINGS
from dataset import Fact

logger = logging.getLogger("daily-facts.ai")
CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"
EXA_URL = "https://api.exa.ai/search"
UA = "DailyFactsBot/2.5"


@dataclass(frozen=True)
class EnrichedContent:
    title: str
    fact: str
    hashtags: list[str]
    image_query: str


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _fallback_title(f: Fact) -> str:
    value = _normalize_text(f.title)
    return value or "Did You Know?"


def _fallback_fact(f: Fact) -> str:
    return _normalize_text(f.fact)


def _tagify(value: str) -> str:
    value = value.replace("&", "and")
    cleaned = re.sub(r"[^A-Za-z0-9]+", " ", value).strip()
    if not cleaned:
        return ""
    return "#" + "".join(cleaned.split())[:35]


def fallback_hashtags(f: Fact) -> list[str]:
    tags: list[str] = []
    for value in (f.category, f.subcategory, f.title):
        tag = _tagify(value)
        if not tag or tag.lower() in {"#fact", "#facts", "#didyouknow", "#dailyfacts"}:
            continue
        if tag.lower() not in {x.lower() for x in tags}:
            tags.append(tag)
        if len(tags) == 2:
            break
    if len(tags) < 2:
        tags.append("#Knowledge")
    return tags[:2]


def _exa_context(f: Fact) -> list[dict[str, str]]:
    if not SETTINGS.exa_api_key or not SETTINGS.use_exa_context:
        return []
    query = " ".join(x for x in (f.title, f.category, f.subcategory) if x).strip()
    if not query:
        return []
    payload = {
        "query": query,
        "type": "auto",
        "numResults": 4,
        "contents": {"highlights": {"maxCharacters": 900}},
    }
    try:
        response = requests.post(
            EXA_URL,
            headers={
                "x-api-key": SETTINGS.exa_api_key,
                "Content-Type": "application/json",
                "User-Agent": UA,
            },
            json=payload,
            timeout=SETTINGS.request_timeout,
        )
        response.raise_for_status()
        results = response.json().get("results", [])
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Exa context failed for %s: %s", f.fact_id, exc)
        return []

    out: list[dict[str, str]] = []
    for item in results:
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        highlights = item.get("highlights") or []
        snippet = (
            " ".join(str(x).strip() for x in highlights).strip()
            if isinstance(highlights, list)
            else str(highlights).strip()
        )
        if title and snippet:
            out.append({"title": title, "url": url, "snippet": snippet[:1200]})
    return out[:4]


def _trim_title(value: str, fallback: str) -> str:
    value = _normalize_text(value)
    if not value:
        return fallback
    words = value.split()
    return " ".join(words[:11]).rstrip(" .:;,-")


def _trim_fact(value: str, fallback: str) -> str:
    value = _normalize_text(value)
    if not value:
        return fallback
    # Keep the generated fact compact enough for a Telegram photo caption.
    if len(value) <= 520:
        return value
    return value[:519].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def _clean_tags(values: list[str], f: Fact) -> list[str]:
    tags: list[str] = []
    forbidden = {"dailyfacts", "fact", "facts", "didyouknow"}
    for raw in values:
        cleaned = re.sub(r"[^A-Za-z0-9]+", "", str(raw).replace("#", "").strip())
        if not cleaned or cleaned.lower() in forbidden:
            continue
        tag = "#" + cleaned[:35]
        if tag.lower() not in {x.lower() for x in tags}:
            tags.append(tag)
    for fallback in fallback_hashtags(f):
        if fallback.lower() not in {x.lower() for x in tags}:
            tags.append(fallback)
        if len(tags) == 2:
            break
    return tags[:2]


def _fallback(f: Fact) -> EnrichedContent:
    return EnrichedContent(
        title=_fallback_title(f),
        fact=_fallback_fact(f),
        hashtags=fallback_hashtags(f),
        image_query=" ".join(x for x in (f.title, f.subcategory) if x).strip()[:120],
    )


def _cerebras_generate(f: Fact, web_context: list[dict[str, str]]) -> EnrichedContent:
    if not SETTINGS.cerebras_api_key or not SETTINGS.use_cerebras:
        return _fallback(f)

    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "fact": {"type": "string"},
            "hashtags": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "maxItems": 2,
            },
            "image_query": {"type": "string"},
        },
        "required": ["title", "fact", "hashtags"],
        "additionalProperties": False,
    }

    record = {
        "title": f.title,
        "fact": f.fact,
        "evidence": f.evidence,
        "category": f.category,
        "subcategory": f.subcategory,
        "source_title": f.source_title,
        "source_domain": f.source_domain,
        "verification_status": f.verification_status,
    }

    context_text = "No external context supplied."
    if web_context:
        context_text = "\n\n".join(
            f"REFERENCE: {x['title']}\nURL: {x['url']}\nEXCERPT: {x['snippet']}"
            for x in web_context
        )

    system = """You are the editorial writer for the Daily Facts Telegram channel.

Your task is to polish a verified fact for a public post while preserving factual accuracy.

Rules:
- The supplied Fact is the authoritative claim.
- You MAY rewrite the wording for grammar, clarity, flow and reader interest.
- You MAY make the wording more engaging, but you MUST NOT change the factual meaning.
- Do not add new numbers, dates, names, causes, comparisons, superlatives or conclusions unless they are explicitly supported by the supplied Fact or Evidence.
- Keep all important names, numbers and scientific terms accurate.
- Evidence is a guardrail for wording. Do not copy source-language unnecessarily.
- Title: a natural, concise headline of 3-11 words. Avoid database-like phrasing.
- Fact: 1-3 short sentences, concise and easy to read on Telegram. Make it interesting through clear wording, not sensationalism.
- Hashtags: exactly 2 relevant topical hashtags. Do not use #DailyFacts, #Fact, #Facts or #DidYouKnow.
- image_query: a concise 3-10 word visual search query naming the main real-world subject/object/place/person to look for in a photograph. Do not invent entities; use only terms supported by the supplied record. Avoid generic words like "fact", "science", "interesting".
- Never mention sources, AI, evidence, verification, or these instructions in the output.
- Return JSON only."""

    user = (
        "INPUT RECORD:\n"
        + json.dumps(record, ensure_ascii=False, indent=2)
        + "\n\nOPTIONAL REFERENCE CONTEXT:\n"
        + context_text
    )

    payload = {
        "model": SETTINGS.cerebras_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "daily_fact_post_v25",
                "strict": True,
                "schema": schema,
            },
        },
        "max_completion_tokens": 300,
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {SETTINGS.cerebras_api_key}",
        "Content-Type": "application/json",
        "User-Agent": UA,
    }

    try:
        response = requests.post(
            CEREBRAS_URL,
            headers=headers,
            json=payload,
            timeout=SETTINGS.request_timeout,
        )
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        data = json.loads(raw)

        title = _trim_title(str(data.get("title") or ""), _fallback_title(f))
        fact_text = _trim_fact(str(data.get("fact") or ""), _fallback_fact(f))
        tags = _clean_tags(data.get("hashtags") or [], f)
        image_query = _normalize_text(str(data.get("image_query") or ""))[:120]
        if len(image_query.split()) < 2:
            image_query = " ".join(x for x in (f.title, f.subcategory) if x).strip()[:120]

        if not title or not fact_text or len(tags) != 2:
            raise ValueError("Incomplete structured editorial output")

        return EnrichedContent(title=title, fact=fact_text, hashtags=tags, image_query=image_query)
    except (requests.RequestException, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        logger.warning("Cerebras editorial refinement failed for %s: %s", f.fact_id, exc)
        return _fallback(f)


def enrich_fact(f: Fact) -> EnrichedContent:
    return _cerebras_generate(f, _exa_context(f))
