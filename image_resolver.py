from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse

import requests

from config import GENERATED_DIR, SETTINGS
from dataset import Fact

logger = logging.getLogger("daily-facts.images")
UA = "DailyFactsBot/1.0"


@dataclass(frozen=True)
class ResolvedImage:
    path: Path
    source_url: str
    source_page: str
    source_name: str
    score: int


def _safe_name(f: Fact) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", f.fact_id or f.title)[:100]


def _get(url: str) -> requests.Response | None:
    try:
        response = requests.get(url, headers={"User-Agent": UA}, timeout=SETTINGS.request_timeout, allow_redirects=True)
        response.raise_for_status()
        return response
    except requests.RequestException as exc:
        logger.debug("GET failed %s: %s", url, exc)
        return None


def _download(url: str, destination: Path) -> bool:
    response = _get(url)
    if not response:
        return False
    content_type = response.headers.get("content-type", "").lower()
    if not content_type.startswith("image/") or len(response.content) < 10_000:
        return False
    destination.write_bytes(response.content)
    return True


def _tokens(value: str) -> set[str]:
    return {x for x in re.findall(r"[a-z0-9]+", (value or "").lower()) if len(x) >= 3}


def _query_variants(f: Fact) -> list[str]:
    subject = f.title.strip() or f.fact[:100]
    variants: list[str] = []
    base_parts = [subject, f.category, f.subcategory]
    exact = " ".join(x for x in base_parts if x).strip()
    if exact:
        variants.append(exact)
    # Prefer visual nouns from title/category, then concise fact subject fallback.
    variants.append(" ".join(x for x in (subject, f.category) if x).strip())
    variants.append(subject)
    fact_words = re.findall(r"[A-Za-z0-9]{4,}", f.fact.lower())[:8]
    if fact_words:
        variants.append(" ".join(fact_words))
    seen: set[str] = set()
    return [q for q in variants if q and not (q.lower() in seen or seen.add(q.lower()))]


def _image_url_from_html(page_url: str, body: str) -> str | None:
    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, body, flags=re.I)
        if match:
            return urljoin(page_url, html.unescape(match.group(1)).strip())
    return None


def _score_candidate(f: Fact, page_title: str, width: int, height: int) -> int:
    title_tokens = _tokens(f.title)
    sub_tokens = _tokens(f.subcategory)
    cat_tokens = _tokens(f.category)
    page_tokens = _tokens(page_title)
    score = 0
    score += 7 * len(title_tokens & page_tokens)
    score += 4 * len(sub_tokens & page_tokens)
    score += 3 * len(cat_tokens & page_tokens)
    if f.title.strip().lower() in page_title.lower():
        score += 10
    if width >= 1200 and height >= 700:
        score += 3
    elif width >= 800 and height >= 500:
        score += 1
    lower = page_title.lower()
    for generic in ("map", "flag", "logo", "icon", "coat of arms", "diagram", "symbol"):
        if generic in lower:
            score -= 7
    return score


def _commons_search(f: Fact, query: str, destination: Path) -> ResolvedImage | None:
    api = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query", "generator": "search", "gsrsearch": query,
        "gsrnamespace": 6, "gsrlimit": SETTINGS.max_image_candidates, "prop": "imageinfo",
        "iiprop": "url|mime|size|extmetadata", "iiurlwidth": 2400, "format": "json",
    }
    try:
        response = requests.get(api, params=params, headers={"User-Agent": UA}, timeout=SETTINGS.request_timeout)
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", {})
    except (requests.RequestException, ValueError) as exc:
        logger.debug("Commons search failed for %r: %s", query, exc)
        return None
    candidates: list[tuple[int, int, str, str, str, str]] = []
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        mime = str(info.get("mime") or "").lower()
        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)
        image_url = info.get("thumburl") or info.get("url")
        meta = info.get("extmetadata") or {}
        license_name = str((meta.get("LicenseShortName") or {}).get("value") or "").strip()
        author = re.sub(r"<[^>]+>", "", str((meta.get("Artist") or {}).get("value") or "")).strip()
        page_title = str(page.get("title") or "")
        if not (mime.startswith("image/") and image_url and width >= 500 and height >= 300 and license_name):
            continue
        score = _score_candidate(f, page_title, width, height)
        candidates.append((score, width * height, image_url, page_title, author, license_name))
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    if not candidates or candidates[0][0] < SETTINGS.image_min_score:
        return None
    score, _, image_url, page_title, author, license_name = candidates[0]
    if not _download(image_url, destination):
        return None
    page_url = "https://commons.wikimedia.org/wiki/" + quote_plus(page_title.replace(" ", "_"))
    credit = "Wikimedia Commons"
    if author:
        credit += f" · {author}"
    if license_name:
        credit += f" · {license_name}"
    return ResolvedImage(destination, image_url, page_url, credit, score)


def _exa_commons_image(f: Fact, destination: Path) -> ResolvedImage | None:
    if not SETTINGS.exa_api_key:
        return None
    query = " ".join(x for x in (f.title, f.subcategory, f.category) if x).strip()
    if not query:
        return None
    payload = {
        "query": f"{query} site:commons.wikimedia.org",
        "type": "auto",
        "numResults": 8,
    }
    try:
        response = requests.post(
            "https://api.exa.ai/search",
            headers={"x-api-key": SETTINGS.exa_api_key, "Content-Type": "application/json", "User-Agent": UA},
            json=payload,
            timeout=SETTINGS.request_timeout,
        )
        response.raise_for_status()
        results = response.json().get("results", [])
    except (requests.RequestException, ValueError) as exc:
        logger.debug("Exa image discovery failed for %s: %s", f.fact_id, exc)
        return None
    best: ResolvedImage | None = None
    for item in results:
        page_url = str(item.get("url") or "")
        host = urlparse(page_url).netloc.lower()
        if not (host == "commons.wikimedia.org" or host.endswith(".commons.wikimedia.org")):
            continue
        page = _get(page_url)
        if not page:
            continue
        image_url = _image_url_from_html(page.url, page.text[:2_000_000])
        if not image_url or not _download(image_url, destination):
            continue
        result = ResolvedImage(destination, image_url, page.url, "Wikimedia Commons", SETTINGS.image_min_score)
        best = result
        break
    return best


def resolve_fact_image(f: Fact) -> ResolvedImage | None:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    cached = GENERATED_DIR / f"{_safe_name(f)}.source"
    for resolver in (_commons_search, _exa_commons_image):
        try:
            if resolver is _commons_search:
                for query in _query_variants(f):
                    result = resolver(f, query, cached)
                    if result:
                        logger.info("Image selected for %s: score=%d source=%s", f.fact_id, result.score, result.source_page)
                        return result
            else:
                result = resolver(f, cached)
                if result:
                    logger.info("Exa fallback image selected for %s", f.fact_id)
                    return result
        except Exception as exc:
            logger.warning("Image resolver failed for %s: %s", f.fact_id, exc)
    return None
