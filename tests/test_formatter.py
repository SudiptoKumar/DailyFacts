from __future__ import annotations

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from content_ai import EnrichedContent
from dataset import Fact
from formatter import format_fallback_caption


def _fact(category: str = "Mystery & Unexplained") -> Fact:
    return Fact(
        fact_id="TEST-1",
        day="01",
        date_value="2026-10-01",
        fact="A verified test fact containing an ampersand & symbol.",
        slot=1,
        month="October",
        title="Test title",
        category=category,
        evidence="Verified test evidence.",
        confidence="high",
        source_url="https://example.com",
        subcategory="Test",
        source_title="Example",
        source_domain="example.com",
        verification_status="Verified",
    )


def test_category_ampersand_is_escaped_once() -> None:
    caption = format_fallback_caption(
        _fact("Mystery & Unexplained"),
        EnrichedContent(
            title="A title & detail",
            fact="A verified fact with an & symbol.",
            hashtags=["Mystery", "Unexplained"],
            image_query="test",
        ),
    )
    assert "MYSTERY &amp; UNEXPLAINED" in caption
    assert "&AMP;" not in caption
    assert "&amp;amp;" not in caption
    assert "A title &amp; detail" in caption
    assert "A verified fact with an &amp; symbol." in caption


def test_common_ampersand_categories() -> None:
    categories = [
        "Mystery & Unexplained",
        "Ocean & Marine",
        "Money & Economics",
        "Mythology & Legends",
        "World Records & Extremes",
    ]
    for category in categories:
        caption = format_fallback_caption(
            _fact(category),
            EnrichedContent("Test", "A verified fact.", ["One", "Two"], "test"),
        )
        assert "&AMP;" not in caption
        assert "&amp;amp;" not in caption
        assert "&amp;" in caption


def run_all() -> None:
    test_category_ampersand_is_escaped_once()
    test_common_ampersand_categories()
    print("formatter regression tests: PASS")


if __name__ == "__main__":
    run_all()
