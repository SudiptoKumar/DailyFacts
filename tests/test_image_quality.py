from __future__ import annotations

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import image_resolver
from dataset import Fact


def _fact() -> Fact:
    return Fact(
        fact_id="TEST-1",
        day="29",
        date_value="2026-10-29",
        fact="About 1.3 million Earths would fit inside the Sun by volume.",
        slot=5,
        month="October",
        title="The Sun's volume compared with Earth",
        category="Space",
        evidence="Verified test evidence.",
        confidence="high",
        source_url="https://science.nasa.gov/sun/facts/",
        subcategory="Sun",
        source_title="NASA",
        source_domain="science.nasa.gov",
        verification_status="Verified",
    )


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self):
        return self._payload


def _page(title: str, mime: str, desc: str) -> dict:
    return {
        "title": title,
        "imageinfo": [{
            "url": "https://upload.wikimedia.org/full",
            "thumburl": "https://upload.wikimedia.org/thumb.jpg",
            "descriptionurl": f"https://commons.wikimedia.org/wiki/{title.replace(' ', '_')}",
            "width": 1200,
            "height": 900,
            "mime": mime,
            "extmetadata": {"ImageDescription": {"value": desc}},
        }],
    }


def test_commons_candidates_exclude_books_and_documents() -> None:
    """Karn's flag (2026-10-29): a fact about the Sun's volume got the first
    page of an 1828 paper as its photo. The 'photo' was a scanned book —
    Commons serves whole PDFs/DjVu scans whose page-1 render the resolver
    treated as a photograph. Only real image files may be candidates."""
    payload = {"query": {"pages": {
        "1": _page("File:On a Method of Comparing the Light of the Sun (IA jstor).pdf",
                   "application/pdf", "Sun Earth volume comparison article from a journal"),
        "2": _page("File:Old astronomy book.djvu",
                   "application/djvu", "Sun Earth volume"),
        "3": _page("File:Sun and Earth size comparison.jpg",
                   "image/jpeg", "The Sun compared with Earth"),
        "4": _page("File:Solar diagram.svg",
                   "image/svg+xml", "Sun Earth diagram"),
    }}}
    original_get = image_resolver.requests.get
    image_resolver.requests.get = lambda url, **kw: _FakeResponse(payload)
    try:
        candidates = image_resolver._commons_candidates("Sun Earth volume", _fact())
    finally:
        image_resolver.requests.get = original_get
    titles = [c[1] for c in candidates]
    assert any("size comparison" in t for t in titles), titles          # real photo kept
    assert any("diagram" in t for t in titles), titles                  # SVG diagram kept
    assert not any(t.endswith((".pdf", ".djvu")) for t in titles), titles  # books/documents gone


def run_all() -> None:
    test_commons_candidates_exclude_books_and_documents()
    print("image quality tests: PASS")


if __name__ == "__main__":
    run_all()
