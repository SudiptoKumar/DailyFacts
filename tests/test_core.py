from datetime import date

from config import normalize_telegram_channel, SETTINGS
from dataset import duplicate_claim_groups, facts_for_date, load_all, validate_dataset
from formatter import build_rich_message, build_rich_message_with_photo, format_fallback_caption
from content_ai import _fallback
from main import unique_pending_facts


def test_dataset_loaded():
    facts = load_all()
    assert len(facts) >= 7000
    assert validate_dataset(facts) == []


def test_date_selection_ignores_csv_year():
    facts = load_all()
    rows = facts_for_date(facts, 1, 1)
    assert len(rows) == 20
    assert rows[0].month == "January"


def test_current_incomplete_month_is_visible():
    facts = load_all()
    assert len(facts_for_date(facts, 10, 1)) == 20


def test_duplicate_claims_are_detected_and_not_selected_twice():
    facts = load_all()
    groups = duplicate_claim_groups(facts)
    assert isinstance(groups, dict)
    rows = facts_for_date(facts, 1, 2)
    state = {"published_fact_ids": {}, "published_claims": {}, "runs": [], "image_cache": {}}
    selected = unique_pending_facts(rows, state, 20)
    assert len({x.claim_key for x in selected}) == len(selected)


def test_rich_message_structure():
    fact = facts_for_date(load_all(), 10, 1)[0]
    content = _fallback(fact)
    rich = build_rich_message(fact, content)
    assert [b["type"] for b in rich["blocks"]] == ["paragraph", "heading", "paragraph", "paragraph", "paragraph", "paragraph", "paragraph"]
    assert "Why it's interesting" in str(rich)
    assert "#DailyFacts" in str(rich)


def test_photo_block():
    fact = facts_for_date(load_all(), 10, 1)[0]
    content = _fallback(fact)
    rich = build_rich_message_with_photo(fact, content)
    assert rich["blocks"][0] == {"type": "photo", "photo": {"type": "photo", "media": "attach://photo"}}


def test_fallback_caption_limit():
    fact = facts_for_date(load_all(), 10, 1)[0]
    content = _fallback(fact)
    caption = format_fallback_caption(fact, content)
    assert len(caption) <= 1024
    assert "#DailyFacts" in caption


def test_channel_normalization():
    assert normalize_telegram_channel("@FactsNewsroom") == "@FactsNewsroom"
    assert normalize_telegram_channel("https://t.me/FactsNewsroom") == "@FactsNewsroom"
    assert normalize_telegram_channel("t.me/FactsNewsroom") == "@FactsNewsroom"
    assert normalize_telegram_channel("-1001234567890") == "-1001234567890"
