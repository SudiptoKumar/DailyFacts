from __future__ import annotations

import argparse
import logging
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config import GENERATED_DIR, LOG_DIR, SETTINGS
from content_ai import EnrichedContent, enrich_fact
from dataset import Fact, facts_for_date, load_all, validate_dataset
from formatter import build_rich_message, format_fallback_caption
from image_pipeline import prepare_fact_image
from image_resolver import resolve_fact_image
from state_store import add_run, cache_image, get_cached_image, is_claim_published, is_published, load_state, mark_published, save_state
from telegram_client import send_message, send_photo, send_rich_message

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("daily-facts")


def today_in_timezone() -> date:
    return datetime.now(ZoneInfo(SETTINGS.timezone)).date()


def resolve_run_date(value: str | None) -> date:
    return date.fromisoformat(value) if value else today_in_timezone()


def unique_pending_facts(day_facts: list[Fact], state: dict, target: int = 1) -> list[Fact]:
    selected: list[Fact] = []
    seen_claims: set[str] = set()
    for fact in day_facts:
        if is_published(state, fact.fact_id):
            continue
        if fact.claim_key in seen_claims or is_claim_published(state, fact.claim_key):
            logger.warning("Skipping duplicate claim: %s", fact.fact_id)
            continue
        seen_claims.add(fact.claim_key)
        selected.append(fact)
        if len(selected) >= target:
            break
    return selected


def cached_image_path(state: dict, fact: Fact) -> tuple[Path | None, dict]:
    entry = get_cached_image(state, fact.fact_id)
    if not entry:
        return None, {}
    raw = entry.get("path")
    if not raw:
        return None, entry
    path = Path(raw)
    if path.exists():
        return path, entry
    return None, entry


def prepare_image(state: dict, fact: Fact):
    cached_path, cached_meta = cached_image_path(state, fact)
    if cached_path:
        return cached_path, cached_meta
    resolved = resolve_fact_image(fact)
    if not resolved:
        return None, {}
    image = prepare_fact_image(fact, resolved)
    if image:
        meta = {
            "path": str(image),
            "source_page": resolved.source_page,
            "source_url": resolved.source_url,
            "credit": resolved.source_name,
            "score": resolved.score,
        }
        if SETTINGS.cache_images:
            cache_image(state, fact.fact_id, meta)
        return image, meta
    return None, {}


def publish_one(fact: Fact, state: dict, *, dry_run: bool = False, skip_image: bool = False) -> bool:
    logger.info("Processing %s | slot=%d | %s", fact.fact_id, fact.slot, fact.title)
    content = enrich_fact(fact)
    image = None
    image_meta: dict = {}
    if not skip_image:
        image, image_meta = prepare_image(state, fact)
    if SETTINGS.image_required and image is None and not dry_run:
        logger.error("No acceptable image for %s and IMAGE_REQUIRED=true", fact.fact_id)
        return False

    if dry_run:
        logger.info("DRY RUN | title=%s | image=%s", content.title, image)
        logger.info("FACT | %s", content.fact)
        logger.info("INTERESTING | %s", content.interesting)
        logger.info("TAGS | %s", " ".join(content.hashtags))
        if image_meta:
            logger.info("IMAGE | %s | score=%s", image_meta.get("source_page"), image_meta.get("score"))
        return True

    send_mode = ""
    try:
        if image:
            # Local files are sent through sendPhoto. Its HTML caption supports
            # the formatting needed by the Daily Facts card and avoids trying to
            # embed a local multipart file through sendRichMessage.
            caption = format_fallback_caption(fact, content)
            message_id = send_photo(str(image), caption)
            send_mode = "photo_html"
        else:
            # Telegram Rich Messages accept an HTML string as InputRichMessage.
            # This keeps rich formatting for text-only fallback posts.
            rich = build_rich_message(fact, content)
            message_id = send_rich_message(rich)
            send_mode = "rich_html"
    except Exception as exc:
        logger.warning("Primary Telegram publish failed for %s: %s", fact.fact_id, exc)
        fallback = format_fallback_caption(fact, content)
        try:
            message_id = send_message(fallback)
            send_mode = "text_html_fallback"
        except Exception as fallback_exc:
            logger.error("Telegram fallback failed for %s: %s", fact.fact_id, fallback_exc)
            return False

    mark_published(
        state,
        fact.fact_id,
        fact.claim_key,
        message_id,
        image_page=image_meta.get("source_page", ""),
        image_credit=image_meta.get("credit", ""),
        image_source="Wikimedia Commons" if image_meta else "",
        send_mode=send_mode,
    )
    save_state(state)
    logger.info("Published %s -> message %s (%s)", fact.fact_id, message_id, send_mode)
    return True


def run(run_date: date, dry_run: bool = False, skip_image: bool = False, max_posts: int | None = None) -> None:
    facts = load_all()
    validation = validate_dataset(facts, strict=SETTINGS.strict_dataset)
    if validation:
        raise RuntimeError("Dataset validation failed:\n" + "\n".join(validation[:40]))

    day_facts = facts_for_date(facts, run_date.month, run_date.day)
    if not day_facts:
        raise RuntimeError(f"No dataset records found for {run_date.strftime('%B %d')}")

    state = load_state()
    target = max_posts if max_posts is not None else SETTINGS.catch_up_max
    pending = unique_pending_facts(day_facts, state, target)
    logger.info("Date=%s | raw=%d | pending=%d | target=%d", run_date, len(day_facts), len(pending), target)
    if len(pending) < target:
        logger.warning(
            "Only %d pending facts are available for this run; target is %d. No filler facts are invented.",
            len(pending), target,
        )
    published = 0
    for fact in pending:
        if publish_one(fact, state, dry_run=dry_run, skip_image=skip_image):
            published += 1
    add_run(state, {"date": run_date.isoformat(), "requested": target, "processed": len(pending), "published_now": published, "dry_run": dry_run})
    if not dry_run:
        save_state(state)
    unique_count = len({f.claim_key for f in day_facts})
    if unique_count < SETTINGS.posts_per_day:
        logger.warning("Only %d unique claims available for %s; no filler facts are invented.", unique_count, run_date.strftime('%B %d'))


def audit() -> None:
    facts = load_all()
    warnings = validate_dataset(facts, strict=True)
    print(f"Total records: {len(facts)}")
    print(f"Strict validation findings: {len(warnings)}")
    for item in warnings[:100]:
        print("-", item)


def preview(run_date: date) -> None:
    facts = load_all()
    day_facts = facts_for_date(facts, run_date.month, run_date.day)
    state = load_state()
    pending = unique_pending_facts(day_facts, state, 20)
    print(f"{run_date.strftime('%B %d')} | raw={len(day_facts)} | unique-pending={len(pending)}")
    for fact in pending:
        print(f"{fact.slot:02d}. [{fact.fact_id}] [{fact.category}] {fact.title}")


def self_test() -> None:
    facts = load_all()
    assert len(facts) >= 7000, f"Unexpectedly small fact database: {len(facts)}"
    # Use month/day rather than the CSV year because the annual database spans publication years.
    day_facts = facts_for_date(facts, 10, 1)
    assert len(day_facts) == 20
    state = {"published_fact_ids": {}, "published_claims": {}, "runs": [], "image_cache": {}}
    pending = unique_pending_facts(day_facts, state, 20)
    assert len(pending) <= 20
    assert len({f.claim_key for f in pending}) == len(pending)
    assert SETTINGS.channel_url.endswith("FactsNewsroom")
    logger.info("Self-test passed with %d records.", len(facts))


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily Facts Telegram bot")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-image", action="store_true")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--date", dest="run_date")
    parser.add_argument("--max-posts", type=int, default=None)
    args = parser.parse_args()

    if args.self_test:
        self_test(); return
    if args.audit:
        audit(); return
    run_date = resolve_run_date(args.run_date)
    if args.preview:
        preview(run_date); return
    run(run_date, dry_run=args.dry_run, skip_image=args.no_image, max_posts=args.max_posts)

if __name__ == "__main__":
    main()
