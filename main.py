from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image

from config import GENERATED_DIR, LOG_DIR, SETTINGS, STATE_FILE
from content_ai import EnrichedContent, enrich_fact, fallback_hashtags
from dataset import Fact, facts_for_exact_date, load_all, validate_dataset
from formatter import format_fallback_caption
from image_pipeline import prepare_fact_image
from image_resolver import resolve_fact_image
from scheduler_cursor import (
    SchedulerCursor,
    advance_after_terminal,
    initialize_cursor,
    refresh_cursor,
    target_for_scheduled_run,
)
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
CURSOR_FILE = Path(__file__).resolve().parent / "state" / "scheduler_cursor.json"


def today_in_timezone() -> date:
    return datetime.now(ZoneInfo(SETTINGS.timezone)).date()


def batch_slot_range(batch: int) -> tuple[int, int]:
    try:
        return BATCHES[batch]
    except KeyError as exc:
        raise ValueError(f"Batch must be 1 or 2, got {batch}") from exc


def _validate_publication_state_file() -> None:
    """Never silently replace a corrupt publication ledger with an empty one."""
    if not STATE_FILE.exists():
        return
    try:
        payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Publication state is unreadable: {STATE_FILE}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Publication state is structurally invalid; refusing to continue to prevent duplicate posts.")
    if "schema_version" in payload and not isinstance(payload.get("schema_version"), int):
        raise RuntimeError("Publication state schema_version is invalid; refusing to continue.")
    if not isinstance(payload.get("dates", {}), dict):
        raise RuntimeError("Publication state dates is invalid; refusing to continue to prevent duplicate posts.")
    if not isinstance(payload.get("image_cache", {}), dict):
        raise RuntimeError("Publication state image_cache is invalid; refusing to continue.")
    if not isinstance(payload.get("runs", []), list):
        raise RuntimeError("Publication state runs is invalid; refusing to continue.")
    for date_key, record in payload.get("dates", {}).items():
        if not isinstance(date_key, str) or not isinstance(record, dict):
            raise RuntimeError("Publication state contains an invalid date record; refusing to continue.")
        if not isinstance(record.get("batches", {}), dict) or not isinstance(record.get("facts", {}), dict):
            raise RuntimeError(f"Publication state date record {date_key!r} is invalid; refusing to continue.")


def load_publication_state() -> dict:
    _validate_publication_state_file()
    return load_state()


def _read_cursor(state: dict, today: date) -> SchedulerCursor:
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not CURSOR_FILE.exists():
        cursor = initialize_cursor(state, today)
        _write_cursor(cursor)
        logger.info(
            "Scheduler cursor initialized | batch1=%s | batch2=%s",
            cursor.next_batch1_date,
            cursor.next_batch2_date,
        )
        return cursor
    try:
        payload = json.loads(CURSOR_FILE.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or int(payload.get("schema_version", 0)) != 1:
            raise ValueError("unsupported scheduler cursor schema")
        cursor = SchedulerCursor.from_dict(payload)
        return cursor
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Scheduler cursor is corrupt: {CURSOR_FILE}: {exc}") from exc


def _write_cursor(cursor: SchedulerCursor) -> None:
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = CURSOR_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(cursor.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(CURSOR_FILE)


def _refresh_and_persist_cursor(state: dict, cursor: SchedulerCursor, today: date) -> SchedulerCursor:
    before = cursor.to_dict()
    cursor = refresh_cursor(state, cursor, today)
    if cursor.to_dict() != before:
        _write_cursor(cursor)
    return cursor


def _unique_allocated(day_facts: list[Fact], batch: int) -> list[Fact]:
    start_slot, end_slot = batch_slot_range(batch)
    selected: list[Fact] = []
    seen_claims: set[str] = set()
    for fact in sorted(day_facts, key=lambda f: (f.slot, f.fact_id)):
        if not start_slot <= fact.slot <= end_slot:
            continue
        if fact.claim_key in seen_claims:
            logger.warning("Skipping duplicate claim in allocated batch %d: %s", batch, fact.fact_id)
            continue
        seen_claims.add(fact.claim_key)
        selected.append(fact)
    return selected


def _pending_from_allocated(
    allocated: list[Fact],
    state: dict,
    run_date: date,
    target: int,
    *,
    ignore_publication_state: bool = False,
) -> list[Fact]:
    selected: list[Fact] = []
    for fact in allocated:
        if not ignore_publication_state and is_fact_published_on_date(state, run_date.isoformat(), fact.fact_id):
            continue
        selected.append(fact)
        if len(selected) >= target:
            break
    return selected


def plan_batch_facts(
    day_facts: list[Fact],
    state: dict,
    run_date: date,
    batch: int,
    target: int = 10,
    *,
    ignore_publication_state: bool = False,
) -> list[Fact]:
    allocated = _unique_allocated(day_facts, batch)
    return _pending_from_allocated(
        allocated, state, run_date, target, ignore_publication_state=ignore_publication_state
    )


def cached_image_path(state: dict, fact: Fact) -> tuple[Path | None, dict]:
    entry = get_cached_image(state, fact.fact_id) or {}
    raw = str(entry.get("path") or "").strip()
    if raw:
        path = Path(raw)
        if path.exists():
            return path, entry
    source_url = str(entry.get("source_url") or "").strip()
    if source_url:
        try:
            from image_resolver import _download
            from image_pipeline import _prepare_without_crop

            source_path = GENERATED_DIR / f"{fact.fact_id}.cached.source"
            output = GENERATED_DIR / f"{fact.fact_id}.jpg"
            if _download(source_url, source_path):
                _prepare_without_crop(source_path, output)
                return output, entry
        except Exception as exc:
            logger.warning("Cached image recovery failed for %s: %s", fact.fact_id, exc)
    return None, entry


def prepare_image(state: dict, fact: Fact, image_query: str = "") -> tuple[Path | None, dict]:
    cached_path, cached_meta = cached_image_path(state, fact)
    if cached_path:
        return cached_path, cached_meta
    try:
        resolved = resolve_fact_image(fact, image_query=image_query)
        if not resolved:
            return None, {}
        image = prepare_fact_image(fact, resolved)
        if not image:
            return None, {}
        meta = {
            "path": str(image),
            "source_page": getattr(resolved, "source_page", ""),
            "source_url": getattr(resolved, "source_url", ""),
            "credit": getattr(resolved, "source_name", ""),
            "score": getattr(resolved, "score", 0),
            "query": image_query,
        }
        if SETTINGS.cache_images:
            cache_image(state, fact.fact_id, meta)
        return image, meta
    except Exception as exc:
        logger.warning("Image pipeline failed for %s: %s. Falling back to text-only.", fact.fact_id, exc)
        return None, {}


def fallback_content(fact: Fact) -> EnrichedContent:
    return EnrichedContent(
        title=fact.title.strip() or "Did You Know?",
        fact=fact.fact.strip(),
        hashtags=fallback_hashtags(fact),
        image_query=" ".join(x for x in (fact.title, fact.subcategory) if x).strip()[:120],
    )


def enrich_safe(fact: Fact) -> EnrichedContent:
    try:
        return enrich_fact(fact)
    except Exception as exc:
        logger.warning("Editorial AI failed unexpectedly for %s: %s. Using deterministic fallback.", fact.fact_id, exc)
        return fallback_content(fact)


def publish_one(
    fact: Fact,
    state: dict,
    run_date: date,
    batch: int,
    *,
    dry_run: bool = False,
    skip_image: bool = False,
    force_republish: bool = False,
    persist_state: bool = True,
) -> bool:
    date_key = run_date.isoformat()
    if not force_republish and is_fact_published_on_date(state, date_key, fact.fact_id):
        logger.info("IDEMPOTENCY SKIP %s | already published for %s", fact.fact_id, date_key)
        return True

    logger.info("Processing %s | date=%s | batch=%d | slot=%d | %s", fact.fact_id, date_key, batch, fact.slot, fact.title)
    content = enrich_safe(fact)
    image = None
    image_meta: dict = {}
    if not skip_image:
        image, image_meta = prepare_image(state, fact, content.image_query)

    if SETTINGS.image_required and image is None and not dry_run:
        reason = "no acceptable image and IMAGE_REQUIRED=true"
        mark_fact_failed(state, date_key, batch, fact.fact_id, reason)
        if persist_state:
            save_state(state)
        return False

    if dry_run:
        caption = format_fallback_caption(fact, content)
        logger.info("DRY RUN | %s | image=%s | caption_len=%d", fact.fact_id, bool(image), len(caption))
        return True

    caption = format_fallback_caption(fact, content)
    try:
        if image:
            message_id = send_photo(str(image), caption)
            send_mode = "photo_html"
        else:
            message_id = send_message(caption)
            send_mode = "text_html"
    except Exception as exc:
        logger.error("Telegram publish failed for %s: %s", fact.fact_id, exc)
        mark_fact_failed(state, date_key, batch, fact.fact_id, str(exc))
        if persist_state:
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
    if persist_state:
        save_state(state)
    logger.info("Published %s -> message %s (%s)", fact.fact_id, message_id, send_mode)
    return True


def _record_run(state: dict, payload: dict) -> None:
    add_run(state, payload)
    save_state(state)


def run(
    run_date: date,
    *,
    batch: int,
    dry_run: bool = False,
    skip_image: bool = False,
    max_posts: int | None = None,
    force_republish: bool = False,
    persist_state: bool = True,
    scheduled_mode: bool = False,
    scheduled_cron: str | None = None,
) -> str:
    facts = load_all()
    validation = validate_dataset(facts, strict=SETTINGS.strict_dataset)
    if validation:
        raise RuntimeError("Dataset validation failed:\n" + "\n".join(validation[:40]))

    day_facts = facts_for_exact_date(facts, run_date)
    if not day_facts:
        raise RuntimeError(f"No dataset records found for exact date {run_date.isoformat()}")

    state = load_publication_state()
    if max_posts is not None and max_posts <= 0:
        raise ValueError("--max-posts must be greater than zero.")
    target = min(max_posts if max_posts is not None else SETTINGS.batch_size, SETTINGS.batch_size)

    if batch == 2 and SETTINGS.block_batch2_without_batch1_receipt and not force_republish:
        if not batch_receipt_valid(state, run_date.isoformat(), 1):
            if scheduled_mode:
                logger.warning(
                    "SAFE NO-OP | Batch 2 date=%s has no terminal Batch 1 receipt yet. The scheduler cursor remains parked.",
                    run_date,
                )
                _record_run(
                    state,
                    {
                        "date": run_date.isoformat(),
                        "batch": 2,
                        "requested": target,
                        "allocated": 0,
                        "processed": 0,
                        "published_now": 0,
                        "status": "waiting_batch1",
                        "dry_run": False,
                        "scheduled_cron": scheduled_cron or "",
                    },
                )
                return "waiting_batch1"
            raise RuntimeError(f"Batch 2 blocked: no valid Batch 1 receipt for {run_date.isoformat()}.")

    allocated = _unique_allocated(day_facts, batch)
    allocated_ids = [f.fact_id for f in allocated]
    if not dry_run and persist_state:
        begin_batch(state, run_date.isoformat(), batch, allocated_ids)
        save_state(state)

    pending = _pending_from_allocated(
        allocated, state, run_date, target, ignore_publication_state=force_republish
    )
    start_slot, end_slot = batch_slot_range(batch)
    logger.info(
        "Date=%s | batch=%d | slots=%d-%d | raw=%d | allocated_unique=%d | pending=%d | target=%d | scheduled_cron=%s",
        run_date,
        batch,
        start_slot,
        end_slot,
        len(day_facts),
        len(allocated),
        len(pending),
        target,
        scheduled_cron or "manual",
    )

    if len(pending) < target:
        logger.warning("Only %d pending unique facts are available in batch %d. No filler facts are invented.", len(pending), batch)

    published_now = 0
    for fact in pending:
        if publish_one(
            fact,
            state,
            run_date,
            batch,
            dry_run=dry_run,
            skip_image=skip_image,
            force_republish=force_republish,
            persist_state=persist_state,
        ):
            published_now += 1

    if dry_run or not persist_state:
        return "dry_run" if dry_run else "test_republish"

    status = finalize_batch(state, run_date.isoformat(), batch, allocated_fact_ids=allocated_ids)
    _record_run(
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
            "dry_run": False,
            "scheduled_cron": scheduled_cron or "",
        },
    )
    logger.info(
        "Batch receipt | date=%s | batch=%d | status=%s | published_now=%d | allocated=%d",
        run_date,
        batch,
        status,
        published_now,
        len(allocated),
    )
    return status


def resolve_run_date_and_mode(
    explicit_date: str | None,
    scheduled_cron: str | None,
    batch: int,
    state: dict,
    cursor: SchedulerCursor,
) -> tuple[date | None, bool, str]:
    today = today_in_timezone()
    if explicit_date:
        return date.fromisoformat(explicit_date), False, "manual-date"
    if scheduled_cron:
        target, reason = target_for_scheduled_run(state, cursor, batch, today)
        return target, True, reason
    return today, False, "manual-current-date"


def advance_cursor_if_terminal(
    *, state: dict, cursor: SchedulerCursor, batch: int, run_date: date, status: str, scheduled_mode: bool
) -> None:
    if not scheduled_mode:
        return
    if status in {"completed", "partial", "empty"}:
        advance_after_terminal(cursor, batch, run_date)
        _write_cursor(cursor)


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
    state = load_publication_state()
    pending = plan_batch_facts(day_facts, state, run_date, batch, SETTINGS.batch_size)
    start_slot, end_slot = batch_slot_range(batch)
    print(f"{run_date.isoformat()} | batch={batch} | slots={start_slot}-{end_slot} | raw={len(day_facts)} | pending={len(pending)}")
    for fact in pending:
        print(f"{fact.slot:02d}. [{fact.fact_id}] [{fact.category}] {fact.title}")


def self_test() -> None:
    from tempfile import TemporaryDirectory
    from scheduler_cursor import SchedulerCursor, target_for_scheduled_run

    facts = load_all()
    assert len(facts) >= 7000, f"Unexpectedly small fact database: {len(facts)}"
    test_date = date(2026, 10, 1)
    day_facts = facts_for_exact_date(facts, test_date)
    assert len(day_facts) == 20, f"Expected 20 facts for {test_date}, got {len(day_facts)}"

    state = {"schema_version": 3, "dates": {}, "runs": [], "image_cache": {}}
    batch1 = plan_batch_facts(day_facts, state, test_date, 1, 10)
    assert [f.slot for f in batch1] == list(range(1, 11))
    begin_batch(state, test_date.isoformat(), 1, [f.fact_id for f in _unique_allocated(day_facts, 1)])
    for fact in batch1:
        mark_fact_published(state, test_date.isoformat(), 1, fact.fact_id, fact.claim_key, 1000 + fact.slot)
    finalize_batch(state, test_date.isoformat(), 1, allocated_fact_ids=[f.fact_id for f in _unique_allocated(day_facts, 1)])
    assert batch_receipt_valid(state, test_date.isoformat(), 1)
    batch2 = plan_batch_facts(day_facts, state, test_date, 2, 10)
    assert [f.slot for f in batch2] == list(range(11, 21))
    assert not ({f.fact_id for f in batch1} & {f.fact_id for f in batch2})

    # Delayed Batch 2 regression: actual runner date is already the next Dhaka day,
    # but the cursor must keep the scheduled publication date.
    delayed_state = {"dates": {
        "2026-09-28": {"batches": {"batch_1": {"status": "completed"}}, "facts": {}}
    }}
    delayed_cursor = SchedulerCursor("2026-09-29", "2026-09-28")
    target, reason = target_for_scheduled_run(delayed_state, delayed_cursor, 2, date(2026, 9, 29))
    assert target == date(2026, 9, 28)
    assert reason == "ready"

    # Batch 2 must wait instead of silently jumping to the next date.
    waiting_cursor = SchedulerCursor("2026-09-29", "2026-09-28")
    waiting_target, waiting_reason = target_for_scheduled_run({"dates": {}}, waiting_cursor, 2, date(2026, 9, 29))
    assert waiting_target is None and waiting_reason == "waiting_for_batch1"

    # Future manual state must never become the scheduled cursor.
    future_state = {"dates": {"2026-10-10": {"batches": {"batch_1": {"status": "completed"}}, "facts": {}}}}
    future_cursor = initialize_cursor(future_state, date(2026, 9, 29))
    assert future_cursor.next_batch1_date == "2026-09-29"

    # Public post shape regression and Telegram caption length.
    sample = batch1[0]
    content = enrich_safe(sample)
    caption = format_fallback_caption(sample, content)
    assert len(caption) <= 1024
    assert "Source:" not in caption
    assert "Why it's interesting" not in caption
    assert "Daily Facts" in caption
    assert "\n\n" in caption
    assert sample.date_value not in caption

    # Native image-ratio regression from the production pipeline.
    from image_pipeline import _prepare_image
    with TemporaryDirectory() as temp:
        src = Path(temp) / "source.jpg"
        out = Path(temp) / "out.jpg"
        Image.new("RGB", (1600, 900), "white").save(src, "JPEG")
        _prepare_image(src, out)
        with Image.open(out) as check:
            assert check.size == (1536, 864)

    logger.info("SELF-TEST PASS | records=%d | scheduler cursor regression | duplicate-safe allocation | caption | native image ratio", len(facts))


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily Facts Telegram bot")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-image", action="store_true")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--date", dest="run_date")
    parser.add_argument("--scheduled-cron", dest="scheduled_cron", help="GitHub Actions cron expression; used to identify a scheduled run")
    parser.add_argument("--batch", type=int, choices=[1, 2], default=None)
    parser.add_argument("--max-posts", type=int, default=None)
    parser.add_argument("--republish-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return
    if args.audit:
        audit()
        return

    state = load_publication_state()
    today = today_in_timezone()
    batch = args.batch
    if batch is None:
        batch = 1 if today.hour < 13 else 2

    cursor = _read_cursor(state, today)
    _refresh_and_persist_cursor(state, cursor, today)

    run_date, scheduled_mode, reason = resolve_run_date_and_mode(
        args.run_date, args.scheduled_cron, batch, state, cursor
    )
    if run_date is None:
        logger.info("SAFE NO-OP | batch=%d | reason=%s | cursor_batch1=%s | cursor_batch2=%s", batch, reason, cursor.next_batch1_date, cursor.next_batch2_date)
        if args.scheduled_cron:
            _record_run(
                state,
                {
                    "date": today.isoformat(),
                    "batch": batch,
                    "requested": args.max_posts or SETTINGS.batch_size,
                    "allocated": 0,
                    "processed": 0,
                    "published_now": 0,
                    "status": reason,
                    "dry_run": False,
                    "scheduled_cron": args.scheduled_cron,
                },
            )
        return

    if args.preview:
        preview(run_date, batch)
        return

    if args.republish_test:
        run(
            run_date,
            batch=batch,
            dry_run=False,
            skip_image=args.no_image,
            max_posts=args.max_posts,
            force_republish=True,
            persist_state=False,
            scheduled_mode=False,
            scheduled_cron=None,
        )
        return

    status = run(
        run_date,
        batch=batch,
        dry_run=args.dry_run,
        skip_image=args.no_image,
        max_posts=args.max_posts,
        scheduled_mode=scheduled_mode,
        scheduled_cron=args.scheduled_cron,
    )
    advance_cursor_if_terminal(
        state=state,
        cursor=cursor,
        batch=batch,
        run_date=run_date,
        status=status,
        scheduled_mode=scheduled_mode and not args.dry_run,
    )


if __name__ == "__main__":
    main()
