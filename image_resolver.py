from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

import requests

from config import GENERATED_DIR, SETTINGS
from dataset import Fact

logger = logging.getLogger("daily-facts.image")

NEGATIVE = ("logo", "icon", "flag", "map", "screenshot", "poster", "collage", "watermark", "avatar")


@dataclass
class ResolvedImage:
    local_path: Path
    output_path: Path
    source_page: str
    source_url: str
    source_name: str
    score: int


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]{3,}", (text or "").casefold())
    return set(words)


def _download(url: str, path: Path) -> bool:
    try:
        response = requests.get(url, timeout=SETTINGS.image_timeout, headers={"User-Agent": "DailyFactsBot/2.14"})
        response.raise_for_status()
        if not response.content:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
        return True
    except Exception as exc:
        logger.debug("image download failed: %s", exc)
        return False


def _score_candidate(text: str, fact: Fact, width: int, height: int) -> int:
    hay = (text or "").casefold()
    title_tokens = _tokens(fact.title)
    fact_tokens = _tokens(fact.fact)
    score = 0
    for token in title_tokens:
        if token in hay:
            score += 4
    for token in _tokens(fact.subcategory):
        if token in hay:
            score += 3
    for token in list(fact_tokens)[:12]:
        if token in hay:
            score += 1
    if fact.category.casefold() in hay:
        score += 2
    if width >= 900 and height >= 600:
        score += 2
    elif width < 400 or height < 300:
        score -= 8
    for bad in NEGATIVE:
        if bad in hay:
            score -= 10
    return score


def _source_page_candidate(
    fact: Fact, query: str, exclude_pages=frozenset(), exclude_urls=frozenset()
) -> tuple[str, str, str, str, int] | None:
    if not fact.source_url.startswith(("http://", "https://")):
        return None
    try:
        response = requests.get(fact.source_url, timeout=SETTINGS.request_timeout, headers={"User-Agent": "DailyFactsBot/2.14"})
        response.raise_for_status()
        html_text = response.text[:2_000_000]
        og = re.search(r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)', html_text, flags=re.I)
        if not og:
            return None
        image_url = html.unescape(og.group(1)).strip()
        # A source article always yields this same og:image. If another fact
        # already used it, skip so the fact falls through to a fresh image.
        if fact.source_url in exclude_pages or image_url in exclude_urls:
            logger.info("Source-page image already used by another fact; skipping for %s", fact.fact_id)
            return None
        title = re.sub(r"<[^>]+>", " ", html_text[:20000])
        score = _score_candidate(f"{query} {fact.title} {title}", fact, 1000, 700)
        if score < SETTINGS.image_min_score:
            return None
        raw_path = GENERATED_DIR / f"{fact.fact_id}.source"
        out_path = GENERATED_DIR / f"{fact.fact_id}.jpg"
        if _download(image_url, raw_path):
            return (str(raw_path), str(out_path), fact.source_url, image_url, score)
    except Exception as exc:
        logger.debug("source page image failed: %s", exc)
    return None


def _commons_candidates(query: str, fact: Fact) -> list[tuple[int, str, str, str]]:
    api = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query", "format": "json", "generator": "search", "gsrsearch": query,
        "gsrnamespace": 6, "gsrlimit": 12, "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": 1200,
    }
    try:
        response = requests.get(api, params=params, timeout=SETTINGS.image_timeout, headers={"User-Agent": "DailyFactsBot/2.14"})
        response.raise_for_status()
        payload = response.json()
        pages = payload.get("query", {}).get("pages", {})
    except Exception as exc:
        logger.debug("Commons search failed: %s", exc)
        return []
    out = []
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        # Commons also hosts whole scanned books/papers (PDF/DjVu). Their
        # "image" is a rendered page of text — never a usable photo, however
        # keyword-rich the book description is. Only real image files count.
        mime = str(info.get("mime") or "").casefold()
        if mime and not mime.startswith("image/"):
            continue
        meta = info.get("extmetadata") or {}
        desc = str((meta.get("ImageDescription") or {}).get("value") or "")
        title = str(page.get("title") or "")
        source_url = str(info.get("descriptionurl") or "")
        image_url = str(info.get("thumburl") or info.get("url") or "")
        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)
        score = _score_candidate(f"{title} {desc}", fact, width, height)
        if image_url:
            out.append((score, title, image_url, source_url))
    return sorted(out, key=lambda x: x[0], reverse=True)


def _exa_candidates(query: str, fact: Fact) -> list[tuple[int, str, str, str]]:
    if not SETTINGS.exa_api_key.strip():
        return []
    url = "https://api.exa.ai/search"
    payload = {
        "query": query,
        "numResults": 8,
        "includeDomains": ["commons.wikimedia.org"],
        "contents": {"text": {"maxCharacters": 3000}},
    }
    try:
        response = requests.post(url, headers={"x-api-key": SETTINGS.exa_api_key, "Content-Type": "application/json"}, json=payload, timeout=SETTINGS.request_timeout)
        response.raise_for_status()
        results = response.json().get("results", [])
    except Exception as exc:
        logger.debug("Exa search failed: %s", exc)
        return []
    candidates = []
    for item in results:
        page_url = str(item.get("url") or "")
        title = str(item.get("title") or "")
        if "commons.wikimedia.org" not in page_url:
            continue
        score = _score_candidate(f"{title} {item.get('text','')}", fact, 1000, 700)
        candidates.append((score, title, page_url, page_url))
    return sorted(candidates, reverse=True)


def _resolve_commons_candidate(candidates, fact: Fact, exclude_pages=frozenset(), exclude_urls=frozenset()) -> ResolvedImage | None:
    fallback = None
    for score, title, image_url, source_page in candidates:
        if score < SETTINGS.image_min_score:
            continue
        if source_page in exclude_pages or image_url in exclude_urls:
            # Already shown with another fact — remember as a last resort
            # only, and keep looking for a fresh image.
            if fallback is None:
                fallback = (score, image_url, source_page)
            continue
        raw = GENERATED_DIR / f"{fact.fact_id}.raw"
        out = GENERATED_DIR / f"{fact.fact_id}.jpg"
        if not _download(image_url, raw):
            continue
        return ResolvedImage(raw, out, source_page, image_url, "Wikimedia Commons", score)
    if fallback is not None:
        score, image_url, source_page = fallback
        logger.warning("Image pool exhausted for %s; reusing %s", fact.fact_id, source_page)
        raw = GENERATED_DIR / f"{fact.fact_id}.raw"
        out = GENERATED_DIR / f"{fact.fact_id}.jpg"
        if _download(image_url, raw):
            return ResolvedImage(raw, out, source_page, image_url, "Wikimedia Commons", score)
    return None


def resolve_fact_image(
    fact: Fact, image_query: str = "", exclude_pages=frozenset(), exclude_urls=frozenset()
) -> ResolvedImage | None:
    query = image_query.strip() or f"{fact.title} {fact.subcategory}".strip()

    source = _source_page_candidate(fact, query, exclude_pages, exclude_urls)
    if source:
        raw, out, page, image_url, score = source
        return ResolvedImage(Path(raw), Path(out), page, image_url, "Source page", score)

    queries = [query, fact.title, f"{fact.title} {fact.category}", f"{fact.subcategory} {fact.title}"]
    all_candidates: list[tuple[int, str, str, str]] = []
    seen: set[str] = set()
    for q in queries:
        if not q or q in seen:
            continue
        seen.add(q)
        all_candidates.extend(_commons_candidates(q, fact))
    best = _resolve_commons_candidate(
        sorted(all_candidates, key=lambda x: x[0], reverse=True), fact, exclude_pages, exclude_urls)
    if best:
        return best

    exa = _exa_candidates(query, fact)
    exa_fallback = None
    for score, title, page_url, _ in exa:
        if page_url.casefold().endswith((".pdf", ".djvu")):
            continue  # scanned book/paper, same exclusion as the Commons pool
        # Fetch the Wikimedia page and discover og:image, keeping Exa as discovery only.
        try:
            response = requests.get(page_url, timeout=SETTINGS.request_timeout, headers={"User-Agent": "DailyFactsBot/2.14"})
            response.raise_for_status()
            og = re.search(r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)', response.text, flags=re.I)
            if not og:
                continue
            image_url = html.unescape(og.group(1)).strip()
            if page_url in exclude_pages or image_url in exclude_urls:
                if exa_fallback is None:
                    exa_fallback = (score, page_url, image_url)
                continue
            raw = GENERATED_DIR / f"{fact.fact_id}.raw"
            out = GENERATED_DIR / f"{fact.fact_id}.jpg"
            if _download(image_url, raw):
                return ResolvedImage(raw, out, page_url, image_url, "Wikimedia Commons via Exa", score)
        except Exception:
            continue
    if exa_fallback is not None:
        score, page_url, image_url = exa_fallback
        raw = GENERATED_DIR / f"{fact.fact_id}.raw"
        out = GENERATED_DIR / f"{fact.fact_id}.jpg"
        if _download(image_url, raw):
            return ResolvedImage(raw, out, page_url, image_url, "Wikimedia Commons via Exa", score)
    return None
