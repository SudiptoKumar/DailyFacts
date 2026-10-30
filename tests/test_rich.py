from __future__ import annotations

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tempfile import TemporaryDirectory

from PIL import Image

from content_ai import EnrichedContent
from dataset import Fact
from formatter import format_rich_html
from image_pipeline import _prepare_image, generate_fallback_card


def _fact(category: str = "Ocean & Marine") -> Fact:
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


def test_rich_html_structure_and_escaping_once() -> None:
    rich = format_rich_html(
        _fact(),
        EnrichedContent(
            title="A title & detail",
            fact="A verified fact with an & symbol.",
            hashtags=["Ocean", "Marine"],
            image_query="test",
        ),
    )
    assert rich.startswith('<img src="tg://photo?id=cover">')
    assert "<h1>A title &amp; detail</h1>" in rich
    assert "OCEAN &amp; MARINE" in rich
    assert "&amp;amp;" not in rich and "&AMP;" not in rich
    assert "A verified fact with an &amp; symbol." in rich
    assert rich.count("<a href=") == 1
    assert "#Ocean #Marine" in rich


def test_prepare_image_keeps_original_ratio_at_fixed_width() -> None:
    with TemporaryDirectory() as tmp:
        # Portrait source: width becomes 1200, height follows the ratio —
        # the photo is NOT cropped to the 1200x675 frame.
        source = Path(tmp) / "source.jpg"
        output = Path(tmp) / "out.jpg"
        Image.new("RGB", (800, 1000), (40, 80, 140)).save(source, format="JPEG", quality=90)
        _prepare_image(source, output)
        with Image.open(output) as img:
            assert img.size == (1200, 1500), img.size
        # Landscape source: same rule.
        source2 = Path(tmp) / "source2.jpg"
        output2 = Path(tmp) / "out2.jpg"
        Image.new("RGB", (1600, 900), (30, 120, 70)).save(source2, format="JPEG", quality=90)
        _prepare_image(source2, output2)
        with Image.open(output2) as img:
            assert img.size == (1200, 675), img.size


def test_fallback_card_short_fact_is_square() -> None:
    with TemporaryDirectory() as tmp:
        output = Path(tmp) / "card.jpg"
        content = EnrichedContent("Test title", "A verified test fact.", ["One", "Two"], "test")
        generate_fallback_card(_fact(), content, output)
        with Image.open(output) as img:
            assert img.size == (1200, 1200), img.size


def test_fallback_card_handles_long_fact() -> None:
    with TemporaryDirectory() as tmp:
        output = Path(tmp) / "card_long.jpg"
        long_fact = ("The Western and Central Europe chronology encompasses present-day Andorra, Austria, "
                     "Belgium, Czech Republic, England, France, Germany, Hungary, Liechtenstein, Luxembourg, "
                     "Moldova, Monaco, Netherlands, Northern Ireland, Poland, the Republic of Ireland, "
                     "Romania, Scotland, Slovakia, Slovenia and Switzerland in one shared timeline.")
        content = EnrichedContent("Western and Central Europe", long_fact, ["History", "Europe"], "test")
        generate_fallback_card(_fact("Ancient Civilizations"), content, output)
        with Image.open(output) as img:
            assert img.size == (1200, 1800), img.size


def run_all() -> None:
    test_rich_html_structure_and_escaping_once()
    test_prepare_image_keeps_original_ratio_at_fixed_width()
    test_fallback_card_short_fact_is_square()
    test_fallback_card_handles_long_fact()
    print("rich regression tests: PASS")


if __name__ == "__main__":
    run_all()
