from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests

from config import GENERATED_DIR, SETTINGS
from dataset import Fact

logger = logging.getLogger("daily-facts.images")
UA = "DailyFactsBot/2.5"

STOPWORDS = {
    "about", "after", "again", "also", "because", "being", "could", "from", "have", "into",
    "more", "most", "that", "their", "there", "these", "they", "this", "those", "through",
    "under", "using", "where", "which", "with", "would", "your", "only", "very", "than", "when",
    "what", "were", "will", "while", "some", "such", "each", "many", "much", "other", "often",
    "known", "used", "made", "make", "called", "like", "daily", "fact", "facts", "the", "and",
    "for", "has", "its", "are", "was", "can", "may", "one", "two", "three", "four", "five",
    "six", "seven", "eight", "nine", "ten", "years", "year", "people", "things", "world",
}

NEGATIVE_TERMS = {
    "map", "maps", "flag", "flags", "logo", "logos", "icon", "icons", "coat of arms", "symbol",
    "diagram", "chart", "graph", "screenshot", "screen capture", "poster", "collage", "montage",
    "stamp", "seal", "watermark", "template", "infographic", "timeline", "locator map",
}
PHOTO_TERMS = {
    "photo", "photograph", "photography", "portrait", "specimen", "close-up", "close up",
    "aerial photograph", "museum object", "microscopy", "microscopic image",
}
ART_TERMS = {
    "painting", "drawing", "illustration", "engraving", "etching", "lithograph", "artwork",
}


@dataclass(frozen=True)
class ImageCandidate:
    image_url: str
    source_page: str
    source_name: str
    page_title: str
    score: int
    area: int
    author: str = ""
    license_name: str = ""


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
        response = requests.get(
            url,
            headers={"User-Agent": UA},
            timeout=SETTINGS.request_timeout,
            allow_redirects=True,
        )
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
    return {
        x for x in re.findall(r"[a-z0-9]+", (value or "").lower())
        if len(x) >= 3 and x not in STOPWORDS
    }


def _important_terms(f: Fact) -> set[str]:
    text = " ".join((f.title, f.subcategory, f.source_title, f.fact))
    tokens = _tokens(text)
    for token in re.findall(r"\b[A-Z][A-Za-z0-9'’-]{2,}\b", text):
        normalized = token.lower().replace("’", "'")
        if normalized not in STOPWORDS:
            tokens.add(re.sub(r"[^a-z0-9]+", "", normalized))
    return {x for x in tokens if x}


def _fallback_image_query(f: Fact) -> str:
    title_terms = [x for x in _tokens(f.title) if len(x) >= 4]
    sub_terms = [x for x in _tokens(f.subcategory) if len(x) >= 4]
    important = sorted(_important_terms(f), key=lambda x: (-len(x), x))
    picked: list[str] = []
    for source in (title_terms, sub_terms, important):
        for term in source:
            if term not in picked:
                picked.append(term)
            if len(picked) >= 7:
                return " ".join(picked)
    return " ".join(picked[:7]) or f.title.strip() or f.fact[:90]


def _query_variants(f: Fact, image_query: str = "") -> list[str]:
    candidates = [
        image_query.strip(),
        f.title.strip(),
        " ".join(x for x in (f.title, f.subcategory) if x).strip(),
        _fallback_image_query(f),
    ]
    fact_terms = [
        x for x in re.findall(r"[A-Za-z0-9][A-Za-z0-9'’-]{3,}", f.fact)
        if x.lower() not in STOPWORDS
    ]
    if fact_terms:
        candidates.append(" ".join(fact_terms[:8]))

    variants: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        value = re.sub(r"\s+", " ", value).strip()
        key = value.lower()
        if value and key not in seen:
            variants.append(value)
            seen.add(key)
    return variants[:4]


def _strip_html(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _meta_value(metadata: dict, key: str) -> str:
    value = metadata.get(key)
    if isinstance(value, dict):
        return _strip_html(str(value.get("value") or ""))
    return _strip_html(str(value or ""))


def _term_present(text: str, term: str) -> bool:
    pattern = r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])"
    return re.search(pattern, text.lower()) is not None


def _metadata_text(page: dict, info: dict) -> str:
    metadata = info.get("extmetadata") or {}
    chunks = [str(page.get("title") or "")]
    for key in ("ObjectName", "ImageDescription", "Credit", "Categories"):
        chunks.append(_meta_value(metadata, key))
    categories = page.get("categories") or []
    chunks.extend(str(x.get("title") or "") for x in categories if isinstance(x, dict))
    return " ".join(x for x in chunks if x)


def _candidate_score(f: Fact, query: str, page: dict, info: dict) -> tuple[int, int]:
    meta_text = _metadata_text(page, info)
    title_tokens = _tokens(f.title)
    query_tokens = _tokens(query)
    important = _important_terms(f)
    meta_tokens = _tokens(meta_text)
    lower = meta_text.lower()

    score = 0
    title_matches = len(title_tokens & meta_tokens)
    query_matches = len(query_tokens & meta_tokens)
    important_matches = len(important & meta_tokens)
    distinct_matches = len((title_tokens | query_tokens | important) & meta_tokens)

    score += min(25, 5 * title_matches)
    score += min(30, 6 * query_matches)
    score += min(28, 4 * important_matches)

    title_phrase = re.sub(r"[^a-z0-9]+", " ", f.title.lower()).strip()
    normalized_title = re.sub(r"[^a-z0-9]+", " ", str(page.get("title") or "").lower()).strip()
    if len(title_phrase) >= 8 and title_phrase in normalized_title:
        score += 22

    if any(_term_present(lower, term) for term in PHOTO_TERMS):
        score += 8
    elif any(_term_present(lower, term) for term in ART_TERMS):
        score -= 1

    negative_hits = sum(1 for term in NEGATIVE_TERMS if _term_present(lower, term))
    score -= 12 * negative_hits

    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    area = max(0, width * height)
    if width >= 1800 and height >= 1000:
        score += 5
    elif width >= 1200 and height >= 700:
        score += 4
    elif width >= 800 and height >= 500:
        score += 2

    megapixels = area / 1_000_000
    if megapixels > 45:
        score -= 1

    if _meta_value(info.get("extmetadata") or {}, "LicenseShortName"):
        score += 3

    if distinct_matches == 0:
        score -= 18
    elif distinct_matches == 1:
        score -= 4

    return score, area


def _commons_candidates(f: Fact, query: str, seen_titles: set[str], image_query: str = "") -> list[ImageCandidate]:
    api = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": 6,
        "gsrlimit": min(SETTINGS.max_image_candidates, 12),
        "prop": "imageinfo|categories",
        "iiprop": "url|mime|size|extmetadata",
        "iiextmetadatafilter": "ImageDescription|ObjectName|Categories|Artist|Credit|LicenseShortName",
        "iiurlwidth": 2400,
        "cllimit": 20,
        "format": "json",
    }
    try:
        response = requests.get(api, params=params, headers={"User-Agent": UA}, timeout=SETTINGS.request_timeout)
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", {})
    except (requests.RequestException, ValueError) as exc:
        logger.debug("Commons search failed for %r: %s", query, exc)
        return []

    out: list[ImageCandidate] = []
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        page_title = str(page.get("title") or "")
        title_key = page_title.lower()
        if title_key in seen_titles:
            continue

        mime = str(info.get("mime") or "").lower()
        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)
        image_url = str(info.get("thumburl") or info.get("url") or "")
        if not (mime.startswith("image/") and image_url and width >= 600 and height >= 400):
            continue

        metadata = info.get("extmetadata") or {}
        license_name = _meta_value(metadata, "LicenseShortName")
        if not license_name:
            continue

        metadata_lower = _metadata_text(page, info).lower()
        negative_hits = sum(1 for term in NEGATIVE_TERMS if _term_present(metadata_lower, term))
        if negative_hits >= 2:
            continue

        score, area = _candidate_score(f, image_query or query, page, info)
        if score < SETTINGS.image_min_score:
            continue

        source_page = str(info.get("descriptionurl") or "").strip()
        if not source_page:
            source_page = "https://commons.wikimedia.org/wiki/" + page_title.replace(" ", "_")
        author = _meta_value(metadata, "Artist")
        source_name = "Wikimedia Commons"
        if author:
            source_name += f" · {author}"
        if license_name:
            source_name += f" · {license_name}"

        out.append(ImageCandidate(
            image_url=image_url,
            source_page=source_page,
            source_name=source_name,
            page_title=page_title,
            score=score,
            area=area,
            author=author,
            license_name=license_name,
        ))
        seen_titles.add(title_key)

    return out


def _exa_candidates(f: Fact, image_query: str = "") -> list[ImageCandidate]:
    if not SETTINGS.exa_api_key:
        return []
    query = image_query.strip() or _fallback_image_query(f)
    payload = {
        "query": f'"{query}" site:commons.wikimedia.org',
        "type": "auto",
        "numResults": 8,
        "contents": {"highlights": {"maxCharacters": 600}},
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
        return []

    q_tokens = _tokens(query)
    fact_terms = _important_terms(f)
    scored: list[tuple[int, str, str]] = []
    for item in results:
        page_url = str(item.get("url") or "")
        host = urlparse(page_url).netloc.lower()
        if not (host == "commons.wikimedia.org" or host.endswith(".commons.wikimedia.org")):
            continue
        title = str(item.get("title") or "").strip()
        highlights = item.get("highlights") or []
        snippet = " ".join(str(x) for x in highlights) if isinstance(highlights, list) else str(highlights)
        metadata = f"{title} {snippet}"
        tokens = _tokens(metadata)
        score = 7 * len(q_tokens & tokens) + 5 * len(fact_terms & tokens)
        if any(_term_present(metadata.lower(), term) for term in NEGATIVE_TERMS):
            score -= 12
        if title:
            score += 3
        scored.append((score, page_url, title))

    candidates: list[ImageCandidate] = []
    for score, page_url, title in sorted(scored, reverse=True):
        if score < 8:
            continue
        page = _get(page_url)
        if not page:
            continue
        image_url = _image_url_from_html(page.url, page.text[:2_000_000])
        if not image_url:
            continue
        candidates.append(ImageCandidate(
            image_url=image_url,
            source_page=page.url,
            source_name="Wikimedia Commons",
            page_title=title,
            score=score,
            area=0,
        ))
        if len(candidates) >= 4:
            break
    return candidates


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
            return html.unescape(match.group(1)).strip()
    return None


def _download_best(candidates: list[ImageCandidate], destination: Path) -> ResolvedImage | None:
    for candidate in sorted(candidates, key=lambda x: (x.score, x.area), reverse=True):
        if _download(candidate.image_url, destination):
            return ResolvedImage(
                path=destination,
                source_url=candidate.image_url,
                source_page=candidate.source_page,
                source_name=candidate.source_name,
                score=candidate.score,
            )
    return None


def resolve_fact_image(f: Fact, image_query: str = "") -> ResolvedImage | None:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    destination = GENERATED_DIR / f"{_safe_name(f)}.source"
    seen_titles: set[str] = set()
    candidates: list[ImageCandidate] = []

    # Search several independent formulations, then choose the best candidate globally.
    for query in _query_variants(f, image_query):
        try:
            candidates.extend(_commons_candidates(f, query, seen_titles, image_query=image_query))
        except Exception as exc:
            logger.debug("Commons candidate search failed for %s query=%r: %s", f.fact_id, query, exc)

    if candidates:
        best = max(candidates, key=lambda x: (x.score, x.area))
        logger.info(
            "Commons image ranking for %s: candidates=%d best_score=%d title=%s",
            f.fact_id,
            len(candidates),
            best.score,
            best.page_title,
        )
        resolved = _download_best(candidates, destination)
        if resolved:
            return resolved

    # Exa is discovery fallback only. It still searches Commons, and results are ranked
    # before the page image is downloaded so the first SERP result is not blindly trusted.
    try:
        exa = _exa_candidates(f, image_query=image_query)
        if exa:
            best = max(exa, key=lambda x: (x.score, x.area))
            logger.info("Exa Commons ranking for %s: candidates=%d best_score=%d title=%s", f.fact_id, len(exa), best.score, best.page_title)
            resolved = _download_best(exa, destination)
            if resolved:
                return resolved
    except Exception as exc:
        logger.debug("Exa Commons resolver failed for %s: %s", f.fact_id, exc)

    logger.warning("No sufficiently relevant image found for %s", f.fact_id)
    return None
