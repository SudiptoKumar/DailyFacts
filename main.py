from __future__ import annotations

import argparse
import logging
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config import GENERATED_DIR, LOG_DIR, SETTINGS
from content_ai import EnrichedContent, enrich_fact
from dataset import Fact, facts_for_exact_date, facts_for_date, load_all, validate_dataset
from formatter import format_fallback_caption
from image_pipeline import prepare_fact_image
from image_resolver import resolve_fact_image
from state_store import (
    add_run,
    batch_receipt_valid,
    begin_batch,
    cache_image,
    finalize_batch,
    get_cached_image,
    is_fact_published_on_date,
    load_state,
    mark_fact_failed,
    mark_fact_published,
    save_state,
)
from telegram_client import send_message, send_photo

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("daily-facts")


BATCHES = {1: (1, 10), 2: (11, 20)}


def today_in_timezone() -> date:
    return datetime.now(ZoneInfo(SETTINGS.timezone)).date()


def resolve_run_date(value: str | None) -> date:
    return date.fromisoformat(value) if value else today_in_timezone()


def normalize_batch(batch: int | None, run_date: date) -> int:
    if batch in BATCHES:
        return batch
    hour = datetime.now(ZoneInfo(SETTINGS.timezone)).hour
    return 1 if hour < 13 else 2


def batch_slot_range(batch: int) -> tuple[int, int]:
    try:
        return BATCHES[batch]
    except KeyError as exc:
        raise ValueError(f"Batch must be 1 or 2, got {batch}") from exc


def plan_batch_facts(day_facts: list[Fact], state: dict, run_date: date, batch: int, target: int = 10) -> list[Fact]:
    start_slot, end_slot = batch_slot_range(batch)
    exact_batch = [f for f in day_facts if start_slot <= f.slot <= end_slot]
    exact_batch.sort(key=lambda f: (f.slot, f.fact_id))

    selected: list[Fact] = []
    seen_claims: set[str] = set()
    for fact in exact_batch:
        if is_fact_published_on_date(state, run_date.isoformat(), fact.fact_id):
            continue
        if fact.claim_key in seen_claims:
            logger.warning("Skipping duplicate claim inside %s batch: %s", f"batch_{batch}", fact.fact_id)
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
    if raw:
        path = Path(raw)
        if path.exists():
            return path, entry
    source_url = str(entry.get("source_url") or "").strip()
    if source_url:
        try:
            from image_resolver import _download
            cached_source = GENERATED_DIR / f"{fact.fact_id}.cached.source"
            if _download(source_url, cached_source):
                from image_pipeline import _prepare_without_crop
                output = GENERATED_DIR / f"{fact.fact_id}.jpg"
                _prepare_without_crop(cached_source, output)
                return output, entry
        except Exception as exc:
            logger.debug("Cached image recovery failed for %s: %s", fact.fact_id, exc)
    return None, entry


def prepare_image(state: dict, fact: Fact, image_query: str = ""):
    cached_path, cached_meta = cached_image_path(state, fact)
    if cached_path:
        return cached_path, cached_meta
    resolved = resolve_fact_image(fact, image_query=image_query)
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
            "query": image_query,
        }
        if SETTINGS.cache_images:
            cache_image(state, fact.fact_id, meta)
        return image, meta
    return None, {}


def publish_one(
    fact: Fact,
    state: dict,
    run_date: date,
    batch: int,
    *,
    dry_run: bool = False,
    skip_image: bool = False,
) -> bool:
    date_key = run_date.isoformat()
    if is_fact_published_on_date(state, date_key, fact.fact_id):
        logger.warning("IDEMPOTENCY SKIP %s | already published for %s", fact.fact_id, date_key)
        return True

    logger.info("Processing %s | date=%s | batch=%d | slot=%d | %s", fact.fact_id, date_key, batch, fact.slot, fact.title)
    content = enrich_fact(fact)
    image = None
    image_meta: dict = {}
    if not skip_image:
        image, image_meta = prepare_image(state, fact, content.image_query)
    if SETTINGS.image_required and image is None and not dry_run:
        reason = "no acceptable image and IMAGE_REQUIRED=true"
        logger.error("%s | %s", fact.fact_id, reason)
        mark_fact_failed(state, date_key, batch, fact.fact_id, reason)
        save_state(state)
        return False

    if dry_run:
        logger.info("DRY RUN | title=%s | image=%s", content.title, image)
        logger.info("FACT | %s", content.fact)
        logger.info("TAGS | %s", " ".join(content.hashtags))
        if image_meta:
            logger.info("IMAGE | %s | score=%s", image_meta.get("source_page"), image_meta.get("score"))
        return True

    try:
        caption = format_fallback_caption(fact, content)
        if image:
            message_id = send_photo(str(image), caption)
            send_mode = "photo_html"
        else:
            message_id = send_message(caption)
            send_mode = "text_html"
    except Exception as exc:
        logger.error("Telegram publish failed for %s: %s", fact.fact_id, exc)
        mark_fact_failed(state, date_key, batch, fact.fact_id, str(exc))
        save_state(state)
        return False

    mark_fact_published(
        state,
        date_key,
        batch,
        fact.fact_id,
        fact.claim_key,
        message_id,
        image_page=image_meta.get("source_page", ""),
        image_credit=image_meta.get("credit", ""),
        image_source="Wikimedia Commons" if image_meta else "",
        send_mode=send_mode,
    )
    save_state(state)
    logger.info(
        "Published %s -> message %s (%s) | idempotency=%s:%s",
        fact.fact_id,
        message_id,
        send_mode,
        date_key,
        fact.fact_id,
    )
    return True


def run(
    run_date: date,
    *,
    batch: int,
    dry_run: bool = False,
    skip_image: bool = False,
    max_posts: int | None = None,
) -> None:
    facts = load_all()
    validation = validate_dataset(facts, strict=SETTINGS.strict_dataset)
    if validation:
        raise RuntimeError("Dataset validation failed:\n" + "\n".join(validation[:40]))

    # Exact calendar date is authoritative. Do not substitute another day's facts.
    day_facts = facts_for_exact_date(facts, run_date)
    if not day_facts:
        raise RuntimeError(f"No dataset records found for exact date {run_date.isoformat()}")

    state = load_state()
    target = max_posts if max_posts is not None else SETTINGS.batch_size
    target = min(target, SETTINGS.batch_size)

    if batch == 2 and SETTINGS.block_batch2_without_batch1_receipt:
        if not batch_receipt_valid(state, run_date.isoformat(), 1):
            raise RuntimeError(
                f"Batch 2 blocked: no valid Batch 1 receipt for {run_date.isoformat()}. "
                "This prevents accidental re-publication after state loss/reset."
            )

    start_slot, end_slot = batch_slot_range(batch)
    allocated = [f for f in day_facts if start_slot <= f.slot <= end_slot]
    allocated.sort(key=lambda f: (f.slot, f.fact_id))
    allocated_ids = [f.fact_id for f in allocated]

    if not dry_run:
        begin_batch(state, run_date.isoformat(), batch, allocated_ids)
        save_state(state)

    pending = plan_batch_facts(day_facts, state, run_date, batch, target)
    logger.info(
        "Date=%s | batch=%d | slots=%d-%d | raw=%d | allocated=%d | pending=%d | target=%d",
        run_date,
        batch,
        start_slot,
        end_slot,
        len(day_facts),
        len(allocated),
        len(pending),
        target,
    )

    if len(pending) < target:
        logger.warning(
            "Only %d pending facts are available in exact batch %d; target is %d. No filler facts are invented.",
            len(pending), batch, target,
        )

    published_now = 0
    for fact in pending:
        if publish_one(fact, state, run_date, batch, dry_run=dry_run, skip_image=skip_image):
            published_now += 1

    status = finalize_batch(state, run_date.isoformat(), batch, allocated_fact_ids=allocated_ids) if not dry_run else "dry_run"
    add_run(
        state,
        {
            "date": run_date.isoformat(),
            "batch": batch,
            "slots": [start_slot, end_slot],
            "requested": target,
            "allocated": len(allocated),
            "processed": len(pending),
            "published_now": published_now,
            "status": status,
            "dry_run": dry_run,
        },
    )
    if not dry_run:
        save_state(state)

    logger.info(
        "Batch receipt | date=%s | batch=%d | status=%s | published_now=%d | allocated=%d",
        run_date,
        batch,
        status,
        published_now,
        len(allocated),
    )


def audit() -> None:
    facts = load_all()
    warnings = validate_dataset(facts, strict=True)
    print(f"Total records: {len(facts)}")
    print(f"Strict validation findings: {len(warnings)}")
    for item in warnings[:100]:
        print("-", item)


def preview(run_date: date, batch: int) -> None:
    facts = load_all()
    day_facts = facts_for_exact_date(facts, run_date)
    state = load_state()
    pending = plan_batch_facts(day_facts, state, run_date, batch, SETTINGS.batch_size)
    start_slot, end_slot = batch_slot_range(batch)
    print(
        f"{run_date.isoformat()} | batch={batch} | slots={start_slot}-{end_slot} | "
        f"raw={len(day_facts)} | pending={len(pending)}"
    )
    for fact in pending:
        print(f"{fact.slot:02d}. [{fact.fact_id}] [{fact.category}] {fact.title}")


def self_test() -> None:
    from formatter import format_fallback_caption

    facts = load_all()
    assert len(facts) >= 7000, f"Unexpectedly small fact database: {len(facts)}"
    test_date = date(2026, 10, 1)
    day_facts = facts_for_exact_date(facts, test_date)
    assert len(day_facts) == 20, f"Expected 20 facts for {test_date}, got {len(day_facts)}"

    state = {"schema_version": 3, "dates": {}, "runs": [], "image_cache": {}}
    batch1 = plan_batch_facts(day_facts, state, test_date, 1, 10)
    assert [f.slot for f in batch1] == list(range(1, 11)), "Batch 1 must use slots 1-10"

    begin_batch(state, test_date.isoformat(), 1, [f.fact_id for f in day_facts if f.slot <= 10])
    for fact in batch1:
        mark_fact_published(state, test_date.isoformat(), 1, fact.fact_id, fact.claim_key, 1000 + fact.slot)
    finalize_batch(state, test_date.isoformat(), 1, allocated_fact_ids=[f.fact_id for f in day_facts if f.slot <= 10])
    assert batch_receipt_valid(state, test_date.isoformat(), 1)

    batch2 = plan_batch_facts(day_facts, state, test_date, 2, 10)
    assert [f.slot for f in batch2] == list(range(11, 21)), "Batch 2 must use slots 11-20"
    assert not ({f.fact_id for f in batch1} & {f.fact_id for f in batch2}), "Batches must never overlap"

    # State-reset simulation: a fresh in-memory state must still plan batch 2 by slot, not by 'first pending'.
    fresh = {"schema_version": 3, "dates": {}, "runs": [], "image_cache": {}}
    fresh_batch2 = plan_batch_facts(day_facts, fresh, test_date, 2, 10)
    assert [f.slot for f in fresh_batch2] == list(range(11, 21)), "Batch 2 must remain deterministic after state reset"

    # Legacy publication-state migration must preserve the current Batch-1 history.
    from state_store import _migrate_legacy_state
    import json, tempfile
    import state_store as _ss
    with tempfile.TemporaryDirectory() as _td:
        _old_dir, _old_file = _ss.STATE_DIR, _ss.STATE_FILE
        _ss.STATE_DIR = Path(_td) / "state"
        _ss.STATE_DIR.mkdir(parents=True, exist_ok=True)
        _ss.STATE_FILE = _ss.STATE_DIR / "publication_state.json"
        legacy_payload = {"published_fact_ids": {
            f"FACT-{test_date.isoformat()}-S{i:02d}": {"message_id": 900 + i, "published_at": "2026-09-24T06:00:00+00:00"}
            for i in range(1, 11)
        }, "published_claims": {}, "runs": [], "image_cache": {}}
        (_ss.STATE_DIR / "posted_state.json").write_text(json.dumps(legacy_payload), encoding="utf-8")
        migrated = _ss.load_state()
        assert _ss.batch_receipt_valid(migrated, test_date.isoformat(), 1)
        _ss.STATE_DIR, _ss.STATE_FILE = _old_dir, _old_file

    sample = batch1[0]
    content = enrich_fact(sample)
    caption = format_fallback_caption(sample, content)
    assert len(caption) <= 1024
    assert "<b>" in caption and "</b>" in caption
    assert content.image_query
    assert SETTINGS.channel_url.endswith("FactsNewsroom")
    assert sample.date_value not in caption
    assert "Source:" not in caption
    assert "Why it's interesting" not in caption

    # Existing image-ranking regression checks.
    from image_resolver import _candidate_score, _term_present
    assert _term_present("a photograph of an octopus", "photograph")
    assert not _term_present("a photograph of an octopus", "graph")
    candidate_page = {
        "title": "File:Common octopus close-up",
        "categories": [{"title": "Category:Octopus"}, {"title": "Category:Cephalopods"}],
    }
    candidate_info = {
        "mime": "image/jpeg", "width": 1800, "height": 1200,
        "extmetadata": {
            "ImageDescription": {"value": "Photograph of an octopus"},
            "ObjectName": {"value": "Common octopus close-up"},
            "LicenseShortName": {"value": "CC BY-SA 4.0"},
        },
    }
    image_score, _ = _candidate_score(sample, "octopus three hearts", candidate_page, candidate_info)
    assert image_score >= SETTINGS.image_min_score

    logger.info("Self-test passed with %d records, deterministic two-batch allocation, and image ranking.", len(facts))


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily Facts Telegram bot")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-image", action="store_true")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--date", dest="run_date")
    parser.add_argument("--batch", type=int, choices=[1, 2], default=None)
    parser.add_argument("--max-posts", type=int, default=None)
    args = parser.parse_args()

    if args.self_test:
        self_test(); return
    if args.audit:
        audit(); return
    run_date = resolve_run_date(args.run_date)
    batch = normalize_batch(args.batch, run_date)
    if args.preview:
        preview(run_date, batch); return
    run(run_date, batch=batch, dry_run=args.dry_run, skip_image=args.no_image, max_posts=args.max_posts)


if __name__ == "__main__":
    main()
