from dataset import Fact
from emoji_engine import category_line, context_label, subject_emojis


def make_fact(**overrides):
    data = dict(
        fact_id="FACT-EMOJI-001",
        day=1,
        date_value="2026-10-01",
        fact="An octopus has three hearts.",
        slot=1,
        month="October",
        title="Octopuses have three hearts",
        category="Animals",
        evidence="The octopus has three hearts.",
        confidence="high",
        source_url="https://example.com",
        subcategory="Marine animals",
        source_title="Example",
        source_domain="example.com",
        verification_status="verified",
    )
    data.update(overrides)
    return Fact(**data)


def test_category_line_uses_configured_icon_and_uppercase():
    assert category_line(make_fact()) == "🐾 ANIMALS"


def test_subject_emoji_selects_relevant_unicode_emoji():
    emojis = subject_emojis(make_fact())
    assert emojis[0] == "🐙"
    assert all("custom_emoji" not in emoji for emoji in emojis)


def test_dynamic_context_label_is_stable():
    fact = make_fact()
    assert context_label(fact) == context_label(fact)
    labels = {
        context_label(make_fact(fact_id=f"FACT-{i}"))
        for i in range(1, 20)
    }
    assert len(labels) >= 2


def test_non_duplicate_fallback_subject_for_category():
    fact = make_fact(
        title="A general entry",
        fact="This is a general science entry.",
        category="Science",
        subcategory="General science",
    )
    assert subject_emojis(fact)


def test_country_fact_gets_country_flag():
    fact = make_fact(
        fact_id="FACT-COUNTRY-001",
        title="Vatican City speaks Italian",
        fact="Nearly everyone in Vatican City speaks Italian.",
        category="Country Facts",
        subcategory="Language distribution",
    )
    assert "🇻🇦" in subject_emojis(fact)
