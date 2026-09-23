from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from config import GENERATED_DIR, SETTINGS
from dataset import Fact

logger = logging.getLogger("daily-facts.images")
UA = "DailyFactsBot/2.7"

STOPWORDS = {
    "about", "after", "again", "also", "because", "being", "could", "from", "have", "into",
    "more", "most", "that", "their", "there", "these", "they", "this", "those", "through",
    "under", "using", "where", "which", "with", "would", "your", "only", "very", "than", "when",
    "what", "were", "will", "while", "some", "such", "each", "many", "much", "other", "often",
    "known", "used", "made", "make", "called", "like", "daily", "fact", "facts", "the", "and",
    "for", "has", "its", "are", "was", "can", "may", "one", "two", "three", "four", "five",
    "six", "seven", "eight", "nine", "ten", "years", "year", "people", "things", "world", "part",
    "associated", "association", "described", "include", "includes", "listed", "current", "purpose",
    "selection", "representation", "additional", "regular", "create", "created", "formation", "formed",
}

HARD_NEGATIVE_TERMS = {
    "logo", "logos", "favicon", "avatar", "site logo", "watermark", "template", "header image",
    "banner", "icon", "icons", "coat of arms", "seal", "screenshot", "screen capture",
}
SOFT_NEGATIVE_TERMS = {
    "map", "maps", "flag", "flags", "diagram", "chart", "graph", "poster", "collage", "montage",
    "stamp", "infographic", "timeline", "locator map",
}
PHOTO_TERMS = {
    "photo", "photograph", "photography", "portrait", "specimen", "close-up", "close up",
    "aerial photograph", "museum object", "microscopy", "microscopic image", "artifact", "artefact",
    "street view", "landscape", "document", "manuscript",
}
ART_TERMS = {
    "painting", "drawing", "illustration", "engraving", "etching", "lithograph", "artwork", "vase",
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
    kind: str = "commons"

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
    try:
        destination.write_bytes(response.content)
        return True
    except OSError:
        return False


def _tokens(value: str) -> set[str]:
    return {x for x in re.findall(r"[a-z0-9]+", (value or "").lower()) if len(x) >= 3 and x not in STOPWORDS}


def _important_terms(f: Fact) -> set[str]:
    text = " ".join((f.title, f.subcategory, f.source_title, f.fact))
    tokens = _tokens(text)
    # Preserve proper nouns and scientific names as high-value terms.
    for token in re.findall(r"\b[A-Z][A-Za-z0-9'’.-]{2,}\b", text):
        normalized = re.sub(r"[^a-z0-9]+", "", token.lower())
        if normalized and normalized not in STOPWORDS:
            tokens.add(normalized)
    return tokens


def _subject_terms(f: Fact) -> list[str]:
    text = " ".join((f.title, f.subcategory, f.source_title, f.fact))
    raw = re.findall(r"[A-Za-z][A-Za-z0-9'’.-]{2,}", text)
    out: list[str] = []
    for token in raw:
        t = re.sub(r"[^a-z0-9]+", "", token.lower())
        if len(t) < 4 or t in STOPWORDS or t in out:
            continue
        out.append(t)
    # Prefer terms from the title/subcategory over generic fact words.
    preferred = []
    for chunk in (f.title, f.subcategory):
        for token in re.findall(r"[A-Za-z][A-Za-z0-9'’.-]{2,}", chunk):
            t = re.sub(r"[^a-z0-9]+", "", token.lower())
            if len(t) >= 4 and t not in STOPWORDS and t not in preferred:
                preferred.append(t)
    return preferred + [x for x in out if x not in preferred]


def _fallback_image_query(f: Fact) -> str:
    terms = _subject_terms(f)
    return " ".join(terms[:8]) or f.title.strip() or f.fact[:90]


def _query_variants(f: Fact, image_query: str = "") -> list[str]:
    subject = _fallback_image_query(f)
    candidates = [
        image_query.strip(),
        f.title.strip(),
        subject,
        " ".join(x for x in (f.title, f.subcategory) if x).strip(),
        " ".join(x for x in (subject, f.category) if x).strip(),
    ]
    # Add one fact-led query that often recovers the concrete object/entity.
    fact_terms = [x for x in re.findall(r"[A-Za-z][A-Za-z0-9'’.-]{3,}", f.fact) if x.lower() not in STOPWORDS]
    if fact_terms:
        candidates.append(" ".join(fact_terms[:8]))

    out: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        value = re.sub(r"\s+", " ", value).strip()
        key = value.lower()
        if value and key not in seen:
            out.append(value)
            seen.add(key)
    return out[:3]


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


def _candidate_score(f: Fact, query: str, page: dict, info: dict, *, kind: str = "commons") -> tuple[int, int]:
    meta_text = _metadata_text(page, info)
    title_tokens = _tokens(f.title)
    query_tokens = _tokens(query)
    important = _important_terms(f)
    meta_tokens = _tokens(meta_text)
    lower = meta_text.lower()

    title_matches = len(title_tokens & meta_tokens)
    query_matches = len(query_tokens & meta_tokens)
    important_matches = len(important & meta_tokens)
    distinct_matches = len((title_tokens | query_tokens | important) & meta_tokens)

    score = 0
    score += min(30, 6 * title_matches)
    score += min(30, 6 * query_matches)
    score += min(25, 4 * important_matches)

    title_phrase = re.sub(r"[^a-z0-9]+", " ", f.title.lower()).strip()
    normalized_title = re.sub(r"[^a-z0-9]+", " ", str(page.get("title") or "").lower()).strip()
    if len(title_phrase) >= 8 and title_phrase in normalized_title:
        score += 24

    if any(_term_present(lower, term) for term in PHOTO_TERMS):
        score += 10
    elif any(_term_present(lower, term) for term in ART_TERMS):
        score += 4

    hard_negative_hits = sum(1 for term in HARD_NEGATIVE_TERMS if _term_present(lower, term))
    soft_negative_hits = sum(1 for term in SOFT_NEGATIVE_TERMS if _term_present(lower, term))
    score -= 30 * hard_negative_hits
    score -= 6 * soft_negative_hits

    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    area = max(0, width * height)
    if width >= 1800 and height >= 1000:
        score += 6
    elif width >= 1200 and height >= 700:
        score += 4
    elif width >= 800 and height >= 500:
        score += 2

    if _meta_value(info.get("extmetadata") or {}, "LicenseShortName"):
        score += 2

    if distinct_matches == 0:
        score -= 25
    elif distinct_matches == 1:
        score -= 2

    if kind == "source":
        # Being an image from the fact's own cited source is a strong relevance signal.
        score += 18

    return score, area


def _commons_candidates(f: Fact, query: str, seen_titles: set[str]) -> list[ImageCandidate]:
    api = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query", "generator": "search", "gsrsearch": query, "gsrnamespace": 6,
        "gsrlimit": min(SETTINGS.max_image_candidates, 16), "prop": "imageinfo|categories",
        "iiprop": "url|mime|size|extmetadata", "iiextmetadatafilter": "ImageDescription|ObjectName|Categories|Artist|Credit|LicenseShortName",
        "iiurlwidth": 2400, "cllimit": 20, "format": "json",
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
        if not (mime.startswith("image/") and image_url and width >= 500 and height >= 300):
            continue
        metadata = info.get("extmetadata") or {}
        license_name = _meta_value(metadata, "LicenseShortName")
        if not license_name:
            continue
        score, area = _candidate_score(f, query, page, info)
        # At least one concrete topic token must match. This prevents generic maps,
        # portraits and decorative images from passing merely because they are photos.
        topic_tokens = _tokens(f.title) | _tokens(f.subcategory) | _tokens(query)
        metadata_tokens = _tokens(_metadata_text(page, info))
        if not (topic_tokens & metadata_tokens):
            continue
        if score < SETTINGS.image_min_score:
            continue
        source_page = str(info.get("descriptionurl") or "").strip() or "https://commons.wikimedia.org/wiki/" + page_title.replace(" ", "_")
        author = _meta_value(metadata, "Artist")
        source_name = "Wikimedia Commons"
        if author:
            source_name += f" · {author}"
        if license_name:
            source_name += f" · {license_name}"
        out.append(ImageCandidate(image_url=image_url, source_page=source_page, source_name=source_name,
                                  page_title=page_title, score=score, area=area, author=author,
                                  license_name=license_name, kind="commons"))
        seen_titles.add(title_key)
    return out


def _html_attr(tag: str, name: str) -> str:
    m = re.search(rf"\b{name}\s*=\s*[\"']([^\"']+)", tag, re.I)
    return html.unescape(m.group(1).strip()) if m else ""


def _source_page_candidates(f: Fact, image_query: str = "") -> list[ImageCandidate]:
    if not f.source_url:
        return []
    response = _get(f.source_url)
    if not response or "text/html" not in response.headers.get("content-type", "").lower():
        return []
    body = response.text[:3_000_000]
    page_title_match = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
    page_title = _strip_html(page_title_match.group(1)) if page_title_match else f.source_title
    meta_pairs: list[tuple[str, str]] = []
    for tag in re.findall(r"<meta\b[^>]*>", body, re.I):
        prop = (_html_attr(tag, "property") or _html_attr(tag, "name")).lower()
        content = _html_attr(tag, "content")
        if prop and content:
            meta_pairs.append((prop, content))
    meta_title = next((v for k, v in meta_pairs if k == "og:title"), "")
    meta_description = next((v for k, v in meta_pairs if k in {"description", "og:description"}), "")
    page_title = " ".join(x for x in (page_title, meta_title, meta_description) if x)

    candidates: list[ImageCandidate] = []
    seen_urls: set[str] = set()

    def add(url: str, context: str) -> None:
        if not url or url.startswith("data:"):
            return
        absolute = urljoin(response.url, url)
        if absolute in seen_urls:
            return
        seen_urls.add(absolute)
        lower_context = (page_title + " " + context + " " + absolute).lower()
        hard_neg = sum(1 for term in HARD_NEGATIVE_TERMS if _term_present(lower_context, term))
        if hard_neg:
            return
        page = {"title": page_title}
        info = {"width": 0, "height": 0, "extmetadata": {"ImageDescription": {"value": context}}}
        score, _ = _candidate_score(f, image_query or _fallback_image_query(f), page, info, kind="source")
        if score < SETTINGS.image_min_score:
            return
        candidates.append(ImageCandidate(image_url=absolute, source_page=response.url, source_name=f.source_domain or "Source",
                                          page_title=page_title, score=score, area=0, kind="source"))

    for prop, content in meta_pairs:
        if prop in {"og:image", "twitter:image", "twitter:image:src"}:
            add(content, "meta image")

    # A small number of content images is enough. Prefer images with meaningful alt/title text.
    for tag in re.findall(r"<img\b[^>]*>", body, re.I)[:80]:
        url = (_html_attr(tag, "src") or _html_attr(tag, "data-src") or _html_attr(tag, "data-original")
               or _html_attr(tag, "data-lazy-src"))
        srcset = _html_attr(tag, "srcset") or _html_attr(tag, "data-srcset")
        if not url and srcset:
            url = srcset.split(",")[0].strip().split(" ", 1)[0]
        alt = _html_attr(tag, "alt")
        title = _html_attr(tag, "title")
        context = f"{alt} {title}"
        if url and context.strip():
            add(url, context)
        if len(candidates) >= 10:
            break
    return sorted(candidates, key=lambda x: (x.score, x.area), reverse=True)[:10]


def _exa_candidates(f: Fact, image_query: str = "") -> list[ImageCandidate]:
    if not SETTINGS.exa_api_key:
        return []
    query = image_query.strip() or _fallback_image_query(f)
    payload = {"query": f'"{query}" site:commons.wikimedia.org', "type": "auto", "numResults": 10,
               "contents": {"highlights": {"maxCharacters": 800}}}
    try:
        response = requests.post("https://api.exa.ai/search", headers={"x-api-key": SETTINGS.exa_api_key, "Content-Type": "application/json", "User-Agent": UA},
                                 json=payload, timeout=SETTINGS.request_timeout)
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
        score -= 18 * sum(1 for term in HARD_NEGATIVE_TERMS if _term_present(metadata.lower(), term))
        score -= 5 * sum(1 for term in SOFT_NEGATIVE_TERMS if _term_present(metadata.lower(), term))
        scored.append((score, page_url, title))
    candidates: list[ImageCandidate] = []
    for score, page_url, title in sorted(scored, reverse=True):
        if score < SETTINGS.image_min_score:
            continue
        page = _get(page_url)
        if not page:
            continue
        image_url = _image_url_from_html(page.url, page.text[:2_000_000])
        if not image_url:
            continue
        candidates.append(ImageCandidate(image_url=image_url, source_page=page.url, source_name="Wikimedia Commons",
                                         page_title=title, score=score, area=0, kind="exa-commons"))
        if len(candidates) >= 5:
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
            logger.info("Selected image for candidate=%s score=%d source=%s", candidate.page_title, candidate.score, candidate.kind)
            return ResolvedImage(path=destination, source_url=candidate.image_url, source_page=candidate.source_page,
                                 source_name=candidate.source_name, score=candidate.score)
    return None


def resolve_fact_image(f: Fact, image_query: str = "") -> ResolvedImage | None:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    destination = GENERATED_DIR / f"{_safe_name(f)}.source"

    # Tier 1: image from the exact evidence/source page. This is the strongest
    # semantic link because the image comes from the same page used to verify the fact.
    try:
        source_candidates = _source_page_candidates(f, image_query=image_query)
        if source_candidates:
            best = source_candidates[0]
            logger.info("Source-page image ranking for %s: candidates=%d best_score=%d title=%s", f.fact_id, len(source_candidates), best.score, best.page_title)
            resolved = _download_best(source_candidates, destination)
            if resolved:
                return resolved
    except Exception as exc:
        logger.debug("Source-page image resolver failed for %s: %s", f.fact_id, exc)

    # Tier 2: multiple Wikimedia Commons searches. Rank globally rather than trusting the first result.
    seen_titles: set[str] = set()
    candidates: list[ImageCandidate] = []
    for query in _query_variants(f, image_query):
        try:
            candidates.extend(_commons_candidates(f, query, seen_titles))
        except Exception as exc:
            logger.debug("Commons candidate search failed for %s query=%r: %s", f.fact_id, query, exc)
    if candidates:
        best = max(candidates, key=lambda x: (x.score, x.area))
        logger.info("Commons image ranking for %s: candidates=%d best_score=%d title=%s", f.fact_id, len(candidates), best.score, best.page_title)
        resolved = _download_best(candidates, destination)
        if resolved:
            return resolved

    # Tier 3: Exa discovery restricted to Commons.
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
