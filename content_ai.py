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
UA = "DailyFactsBot/2.0"


@dataclass(frozen=True)
class EnrichedContent:
    title: str
    fact: str
    interesting: str
    hashtags: list[str]


def _fallback(f: Fact) -> EnrichedContent:
    title = f.title.strip() or "Did You Know?"
    fact = re.sub(r"\s+", " ", f.fact.strip())
    interesting = _interesting_fallback(f)
    tags = fallback_hashtags(f)
    return EnrichedContent(title=title, fact=fact, interesting=interesting, hashtags=tags)


def _interesting_fallback(f: Fact) -> str:
    if f.evidence:
        text = re.sub(r"\s+", " ", f.evidence.strip())
        if len(text) <= 220:
            return text.rstrip(".") + "."
    return f"This fact is part of {f.subcategory or f.category.lower()} knowledge."


def _tagify(value: str) -> str:
    value = value.replace("&", "and")
    cleaned = re.sub(r"[^A-Za-z0-9]+", " ", value).strip()
    if not cleaned:
        return ""
    parts = cleaned.split()
    return "#" + "".join(parts)[:35]


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
    return tags


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
        "contents": {"highlights": {"maxCharacters": 1000}},
    }
    try:
        response = requests.post(
            EXA_URL,
            headers={"x-api-key": SETTINGS.exa_api_key, "Content-Type": "application/json", "User-Agent": UA},
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
        snippet = " ".join(str(x).strip() for x in highlights).strip() if isinstance(highlights, list) else str(highlights).strip()
        if title and snippet:
            out.append({"title": title, "url": url, "snippet": snippet[:1300]})
    return out[:4]


def _trim_title(value: str, fallback: str) -> str:
    value = re.sub(r"\s+", " ", value.strip())
    if not value:
        return fallback
    words = value.split()
    return " ".join(words[:10]).rstrip(" .:;,-")


def _trim_interesting(value: str) -> str:
    value = re.sub(r"\s+", " ", value.strip())
    if len(value) <= 240:
        return value
    return value[:239].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


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
        if len(tags) == 3:
            break
    return tags[:2]


def _cerebras_generate(f: Fact, web_context: list[dict[str, str]]) -> EnrichedContent:
    if not SETTINGS.cerebras_api_key or not SETTINGS.use_cerebras:
        return _fallback(f)
    schema = {
        "type": "object",
        "properties": {
            "interesting": {"type": "string"},
            "hashtags": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 3},
        },
        "required": ["interesting", "hashtags"],
        "additionalProperties": False,
    }
    record = {
        "title": f.title,
        "fact": f.fact,
        "category": f.category,
        "subcategory": f.subcategory,
        "source_title": f.source_title,
        "source_domain": f.source_domain,
        "verification_status": f.verification_status,
    }
    context_text = "No external context supplied."
    if web_context:
        context_text = "\n\n".join(f"SOURCE: {x['title']}\nURL: {x['url']}\nEXCERPT: {x['snippet']}" for x in web_context)
    system = """You write concise fact posts for the Daily Facts Telegram channel.
Rules:
- The CSV Title and Fact are authoritative and will be published unchanged.
- Never rewrite, add to, correct, or speculate on the factual claim.
- External context may only clarify why the supplied fact is notable, and only when it directly supports the supplied fact.
- Interesting: one short sentence explaining why the fact is notable, using only information supported by the supplied fact/evidence/context.
- Hashtags: exactly 2 topical hashtags. Do not use #DailyFacts or generic #Fact/#Facts because the footer supplies the Daily Facts brand separately.
- Do not mention sources, evidence, AI, or these instructions in the text.
Return JSON only."""
    user = "INPUT RECORD:\n" + json.dumps(record, ensure_ascii=False, indent=2) + "\n\nOPTIONAL CONTEXT:\n" + context_text
    payload = {
        "model": SETTINGS.cerebras_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "daily_fact_post", "strict": True, "schema": schema}},
        "max_completion_tokens": 350,
        "temperature": 0.25,
    }
    headers = {"Authorization": f"Bearer {SETTINGS.cerebras_api_key}", "Content-Type": "application/json", "User-Agent": UA}
    try:
        response = requests.post(CEREBRAS_URL, headers=headers, json=payload, timeout=SETTINGS.request_timeout)
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        data = json.loads(raw)
        title = f.title.strip() or "Did You Know?"
        fact_text = re.sub(r"\s+", " ", f.fact.strip())
        interesting = _trim_interesting(str(data.get("interesting") or "").strip()) or _interesting_fallback(f)
        tags = _clean_tags(data.get("hashtags") or [], f)
        if not title or not fact_text or not interesting or len(tags) != 2:
            raise ValueError("Incomplete structured output")
        return EnrichedContent(title=title, fact=fact_text, interesting=interesting, hashtags=tags)
    except (requests.RequestException, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        logger.warning("Cerebras enrichment failed for %s: %s", f.fact_id, exc)
        return _fallback(f)


def enrich_fact(f: Fact) -> EnrichedContent:
    return _cerebras_generate(f, _exa_context(f))
