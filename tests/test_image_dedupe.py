from __future__ import annotations

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import image_resolver
from dataset import Fact
from state_store import used_image_keys


def _fact(fact_id: str = "TEST-1", title: str = "The Sun", source_url: str = "https://example.com/sun") -> Fact:
    return Fact(
        fact_id=fact_id,
        day="01",
        date_value="2026-10-01",
        fact="A verified test fact about the Sun and its surface.",
        slot=1,
        month="October",
        title=title,
        category="Space & Astronomy",
        evidence="Verified test evidence.",
        confidence="high",
        source_url=source_url,
        subcategory="Sun",
        source_title="Example",
        source_domain="example.com",
        verification_status="Verified",
    )


def _state() -> dict:
    return {
        "schema_version": 4,
        "dates": {
            "2026-09-30": {"batches": {}, "facts": {
                "OLD-1": {"status": "published", "image_page": "https://commons.wikimedia.org/wiki/File:Sun_A.jpg"},
                "OLD-2": {"status": "failed", "image_page": "https://commons.wikimedia.org/wiki/File:Never_Posted.jpg"},
            }},
        },
        "image_cache": {
            "OLD-3": {"source_page": "https://commons.wikimedia.org/wiki/File:Sun_B.jpg",
                      "source_url": "https://upload.wikimedia.org/sun_b.jpg"},
            "TEST-1": {"source_page": "https://commons.wikimedia.org/wiki/File:Own.jpg",
                       "source_url": "https://upload.wikimedia.org/own.jpg"},
        },
        "runs": [],
        "published_claims": {},
    }


def test_used_image_keys_collects_history_and_skips_own_fact() -> None:
    pages, urls = used_image_keys(_state(), exclude_fact_id="TEST-1")
    assert "https://commons.wikimedia.org/wiki/File:Sun_A.jpg" in pages      # published record
    assert "https://commons.wikimedia.org/wiki/File:Sun_B.jpg" in pages      # another fact's cache
    assert "https://upload.wikimedia.org/sun_b.jpg" in urls                  # another fact's cache URL
    assert "https://commons.wikimedia.org/wiki/File:Never_Posted.jpg" not in pages  # failed != used
    assert "https://commons.wikimedia.org/wiki/File:Own.jpg" not in pages    # own fact excluded
    assert "https://upload.wikimedia.org/own.jpg" not in urls


def test_commons_pick_skips_used_images() -> None:
    candidates = [
        (40, "File:Sun A", "https://upload.wikimedia.org/a.jpg", "https://commons.wikimedia.org/wiki/File:Sun_A.jpg"),
        (30, "File:Sun B", "https://upload.wikimedia.org/b.jpg", "https://commons.wikimedia.org/wiki/File:Sun_B.jpg"),
        (20, "File:Sun C", "https://upload.wikimedia.org/c.jpg", "https://commons.wikimedia.org/wiki/File:Sun_C.jpg"),
    ]
    original_download = image_resolver._download
    image_resolver._download = lambda url, path: True
    try:
        picked = image_resolver._resolve_commons_candidate(
            candidates, _fact(),
            exclude_pages={"https://commons.wikimedia.org/wiki/File:Sun_A.jpg"},
            exclude_urls={"https://upload.wikimedia.org/b.jpg"},
        )
    finally:
        image_resolver._download = original_download
    assert picked is not None
    assert picked.source_page == "https://commons.wikimedia.org/wiki/File:Sun_C.jpg", picked


def test_commons_pick_reuses_best_only_when_pool_exhausted() -> None:
    candidates = [
        (40, "File:Sun A", "https://upload.wikimedia.org/a.jpg", "https://commons.wikimedia.org/wiki/File:Sun_A.jpg"),
    ]
    original_download = image_resolver._download
    image_resolver._download = lambda url, path: True
    try:
        picked = image_resolver._resolve_commons_candidate(
            candidates, _fact(),
            exclude_pages={"https://commons.wikimedia.org/wiki/File:Sun_A.jpg"},
            exclude_urls=set(),
        )
    finally:
        image_resolver._download = original_download
    assert picked is not None  # last resort: a repeat beats no image at all
    assert picked.source_page == "https://commons.wikimedia.org/wiki/File:Sun_A.jpg"


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text
        self.content = b"fake-image-bytes"

    def raise_for_status(self) -> None:
        pass


def test_source_page_image_skipped_when_article_already_produced_one() -> None:
    page_html = ('<html><head><meta property="og:image" content="https://example.com/og.jpg"></head>'
                 '<body>All about the Sun and its surface — a verified test fact resource.</body></html>')
    original_get = image_resolver.requests.get
    image_resolver.requests.get = lambda url, **kw: _FakeResponse(page_html)
    try:
        skipped = image_resolver._source_page_candidate(
            _fact(), "Sun", exclude_pages={"https://example.com/sun"}, exclude_urls=set())
        allowed = image_resolver._source_page_candidate(
            _fact(), "Sun", exclude_pages=set(), exclude_urls=set())
    finally:
        image_resolver.requests.get = original_get
    assert skipped is None        # article already gave an image to another fact
    assert allowed is not None    # same article is fine when unused
    assert allowed[3] == "https://example.com/og.jpg"  # candidate carries the real image URL


def test_two_facts_sharing_a_pool_get_different_images() -> None:
    """Karn's complaint, at resolver level: same-topic facts must not keep
    winning the same top-scoring Commons image."""
    pool = [
        (40, "File:Sun A", "https://upload.wikimedia.org/a.jpg", "https://commons.wikimedia.org/wiki/File:Sun_A.jpg"),
        (30, "File:Sun B", "https://upload.wikimedia.org/b.jpg", "https://commons.wikimedia.org/wiki/File:Sun_B.jpg"),
    ]
    original_candidates = image_resolver._commons_candidates
    original_download = image_resolver._download
    original_source = image_resolver._source_page_candidate
    image_resolver._commons_candidates = lambda q, f: list(pool)
    image_resolver._download = lambda url, path: True
    image_resolver._source_page_candidate = lambda fact, query, exclude_pages=frozenset(), exclude_urls=frozenset(): None
    try:
        first = image_resolver.resolve_fact_image(_fact("TEST-1"), "Sun")
        assert first is not None
        second = image_resolver.resolve_fact_image(
            _fact("TEST-2"), "Sun",
            exclude_pages={first.source_page}, exclude_urls={first.source_url})
    finally:
        image_resolver._commons_candidates = original_candidates
        image_resolver._download = original_download
        image_resolver._source_page_candidate = original_source
    assert second is not None
    assert second.source_page != first.source_page, (first, second)


def run_all() -> None:
    test_used_image_keys_collects_history_and_skips_own_fact()
    test_commons_pick_skips_used_images()
    test_commons_pick_reuses_best_only_when_pool_exhausted()
    test_source_page_image_skipped_when_article_already_produced_one()
    test_two_facts_sharing_a_pool_get_different_images()
    print("image dedupe tests: PASS")


if __name__ == "__main__":
    run_all()
