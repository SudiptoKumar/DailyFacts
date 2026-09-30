from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import requests

from config import SETTINGS
from dataset import Fact

logger = logging.getLogger("daily-facts.ai")


@dataclass(frozen=True)
class EnrichedContent:
    title: str
    fact: str
    hashtags: list[str]
    image_query: str


def _hashtags(fact: Fact, raw: object = None) -> list[str]:
    out: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                tag = re.sub(r"[^A-Za-z0-9_]", "", item.lstrip("#")).strip()
                if tag and tag.lower() not in {x.lower().lstrip("#") for x in out}:
                    out.append(tag)
    candidates = [fact.category, fact.subcategory, *fact.title.split()]
    for item in candidates:
        tag = re.sub(r"[^A-Za-z0-9]", "", item or "").strip()
        if len(tag) >= 3 and tag.lower() != "dailyfacts" and tag.lower() not in {x.lower().lstrip("#") for x in out}:
            out.append(tag)
        if len(out) == 2:
            break
    while len(out) < 2:
        out.append("Facts")
    return out[:2]


def fallback_hashtags(fact: Fact) -> list[str]:
    return _hashtags(fact)


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text, flags=re.I).strip()
    text = re.sub(r"```$", "", text).strip()
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError("AI did not return a JSON object")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("AI JSON is not an object")
    return value


def enrich_fact(fact: Fact) -> EnrichedContent:
    if not SETTINGS.use_cerebras or not SETTINGS.cerebras_api_key.strip():
        return EnrichedContent(fact.title, fact.fact, fallback_hashtags(fact), f"{fact.title} {fact.subcategory}"[:180])

    url = "https://api.cerebras.ai/v1/chat/completions"
    system = (
        "You are an editorial formatter for a factual Telegram channel. "
        "The database fact and evidence are the source of truth. Never add facts, numbers, dates, names, causes, "
        "comparisons, or scientific details not supported by the supplied database text. "
        "Return JSON only with title, fact, hashtags, image_query."
    )
    user = {
        "title": fact.title,
        "fact": fact.fact,
        "evidence": fact.evidence,
        "category": fact.category,
        "subcategory": fact.subcategory,
        "source_title": fact.source_title,
    }
    payload = {
        "model": SETTINGS.cerebras_model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {SETTINGS.cerebras_api_key}", "Content-Type": "application/json"}
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=SETTINGS.request_timeout)
        if response.status_code == 429:
            raise RuntimeError("Cerebras rate limit (429), using deterministic fallback")
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        obj = _extract_json(content)
        title = str(obj.get("title") or fact.title).strip() or fact.title
        refined_fact = str(obj.get("fact") or fact.fact).strip() or fact.fact
        hashtags = _hashtags(fact, obj.get("hashtags"))
        image_query = str(obj.get("image_query") or f"{fact.title} {fact.subcategory}").strip()[:180]
        # Do not allow an empty or obviously unrelated AI fact.
        if len(refined_fact) < 20 or len(title) < 3:
            raise ValueError("AI returned unusable editorial text")
        return EnrichedContent(title, refined_fact, hashtags, image_query)
    except Exception as exc:
        logger.warning("Cerebras unavailable for %s: %s", fact.fact_id, exc)
        return EnrichedContent(fact.title, fact.fact, fallback_hashtags(fact), f"{fact.title} {fact.subcategory}"[:180])
