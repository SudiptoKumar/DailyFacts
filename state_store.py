from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from config import GENERATED_DIR, SETTINGS, STATE_DIR, STATE_FILE

SCHEMA_VERSION = 3
BATCH_NAMES = {1: "batch_1", 2: "batch_2"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fresh_state() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "dates": {},
        "image_cache": {},
        "runs": [],
    }


def _migrate_legacy_state() -> dict | None:
    legacy_path = STATE_DIR / "posted_state.json"
    if STATE_FILE.exists() or not legacy_path.exists():
        return None
    try:
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(legacy, dict):
        return None

    migrated = _fresh_state()
    migrated["migration"] = {"source": "posted_state.json", "migrated_at": _now()}
    for fact_id, info in (legacy.get("published_fact_ids") or {}).items():
        if not isinstance(info, dict):
            continue
        match = re.match(r"^FACT-(\d{4}-\d{2}-\d{2})-S(\d+)$", str(fact_id))
        if not match:
            continue
        date_key = match.group(1)
        slot = int(match.group(2))
        batch = 1 if slot <= 10 else 2
        record = _date_record(migrated, date_key)
        record["facts"][str(fact_id)] = {
            "status": "published",
            "batch": batch,
            "claim_key": "",
            "message_id": info.get("message_id"),
            "published_at": info.get("published_at") or _now(),
            "image_page": info.get("image_page", ""),
            "image_credit": info.get("image_credit", ""),
            "image_source": info.get("image_source", ""),
            "send_mode": info.get("send_mode", ""),
            "idempotency_key": f"{date_key}:{fact_id}",
        }
        b = record["batches"].setdefault(batch_name(batch), {
            "status": "started", "allocated_fact_ids": [], "published_fact_ids": [],
            "failed_fact_ids": [], "started_at": info.get("published_at") or _now(), "completed_at": "",
        })
        if str(fact_id) not in b["published_fact_ids"]:
            b["published_fact_ids"].append(str(fact_id))
        if slot not in range(1, 21):
            continue
        # Legacy state contains only successfully published IDs. Infer a valid batch-1
        # receipt only when every first-batch slot is represented. This is deliberately
        # strict so an incomplete historical state cannot unlock Batch 2 by accident.
        if batch == 1:
            slots_in_batch = {
                int(re.match(r"^FACT-\d{4}-\d{2}-\d{2}-S(\d+)$", x).group(1))
                for x in b["published_fact_ids"]
                if re.match(r"^FACT-\d{4}-\d{2}-\d{2}-S(\d+)$", x)
            }
            if set(range(1, 11)).issubset(slots_in_batch):
                b["status"] = "completed"
                dates = [x.get("published_at", "") for x in (legacy.get("published_fact_ids") or {}).values() if isinstance(x, dict)]
                b["completed_at"] = max(dates) if dates else _now()
            else:
                b["status"] = "partial"
        else:
            b["status"] = "partial"
    legacy_cache = legacy.get("image_cache")
    if isinstance(legacy_cache, dict):
        migrated["image_cache"].update(legacy_cache)
    return migrated


def load_state() -> dict:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    migrated = _migrate_legacy_state()
    if migrated is not None:
        return migrated
    if not STATE_FILE.exists():
        return _fresh_state()
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _fresh_state()
        dates = data.get("dates") if isinstance(data.get("dates"), dict) else {}
        image_cache = data.get("image_cache") if isinstance(data.get("image_cache"), dict) else {}
        runs = data.get("runs") if isinstance(data.get("runs"), list) else []
        return {
            "schema_version": int(data.get("schema_version", SCHEMA_VERSION)),
            "dates": dates,
            "image_cache": image_cache,
            "runs": runs[-100:],
        }
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return _fresh_state()


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    normalized = {
        "schema_version": SCHEMA_VERSION,
        "dates": state.get("dates", {}),
        "image_cache": state.get("image_cache", {}),
        "runs": state.get("runs", [])[-100:],
    }
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATE_FILE)


def _date_record(state: dict, date_key: str) -> dict:
    dates = state.setdefault("dates", {})
    record = dates.setdefault(
        date_key,
        {
            "batches": {},
            "facts": {},
        },
    )
    record.setdefault("batches", {})
    record.setdefault("facts", {})
    return record


def batch_name(batch: int) -> str:
    if batch not in BATCH_NAMES:
        raise ValueError(f"Unsupported batch: {batch}")
    return BATCH_NAMES[batch]


def begin_batch(state: dict, date_key: str, batch: int, fact_ids: list[str]) -> None:
    record = _date_record(state, date_key)
    key = batch_name(batch)
    existing = record["batches"].get(key) or {}
    # Re-running a batch is safe. Keep previously published IDs and refresh the allocation.
    record["batches"][key] = {
        "status": existing.get("status", "started"),
        "allocated_fact_ids": list(fact_ids),
        "published_fact_ids": list(existing.get("published_fact_ids") or []),
        "failed_fact_ids": list(existing.get("failed_fact_ids") or []),
        "started_at": existing.get("started_at") or _now(),
        "completed_at": existing.get("completed_at", ""),
    }


def batch_status(state: dict, date_key: str, batch: int) -> str:
    record = _date_record(state, date_key)
    return str((record["batches"].get(batch_name(batch)) or {}).get("status") or "unrecorded")


def batch_receipt_valid(state: dict, date_key: str, batch: int) -> bool:
    return batch_status(state, date_key, batch) in {"completed", "partial", "empty"}


def published_fact_ids_for_date(state: dict, date_key: str) -> set[str]:
    record = _date_record(state, date_key)
    facts = record.get("facts") or {}
    return {
        fact_id
        for fact_id, info in facts.items()
        if isinstance(info, dict) and info.get("status") == "published"
    }


def is_fact_published_on_date(state: dict, date_key: str, fact_id: str) -> bool:
    record = _date_record(state, date_key)
    info = (record.get("facts") or {}).get(fact_id)
    return isinstance(info, dict) and info.get("status") == "published"


def mark_fact_published(
    state: dict,
    date_key: str,
    batch: int,
    fact_id: str,
    claim_key: str,
    message_id: int | None,
    *,
    image_page: str = "",
    image_credit: str = "",
    image_source: str = "",
    send_mode: str = "",
) -> None:
    record = _date_record(state, date_key)
    record["facts"][fact_id] = {
        "status": "published",
        "batch": batch,
        "claim_key": claim_key,
        "message_id": message_id,
        "published_at": _now(),
        "image_page": image_page,
        "image_credit": image_credit,
        "image_source": image_source,
        "send_mode": send_mode,
        "idempotency_key": f"{date_key}:{fact_id}",
    }
    b = record["batches"].setdefault(batch_name(batch), {})
    published = list(b.get("published_fact_ids") or [])
    if fact_id not in published:
        published.append(fact_id)
    b["published_fact_ids"] = published
    failed = [x for x in list(b.get("failed_fact_ids") or []) if x != fact_id]
    b["failed_fact_ids"] = failed


def mark_fact_failed(state: dict, date_key: str, batch: int, fact_id: str, reason: str) -> None:
    record = _date_record(state, date_key)
    record["facts"][fact_id] = {
        "status": "failed",
        "batch": batch,
        "reason": reason,
        "failed_at": _now(),
        "idempotency_key": f"{date_key}:{fact_id}",
    }
    b = record["batches"].setdefault(batch_name(batch), {})
    failed = list(b.get("failed_fact_ids") or [])
    if fact_id not in failed:
        failed.append(fact_id)
    b["failed_fact_ids"] = failed


def finalize_batch(state: dict, date_key: str, batch: int, *, allocated_fact_ids: list[str]) -> str:
    record = _date_record(state, date_key)
    key = batch_name(batch)
    b = record["batches"].setdefault(key, {})
    b["allocated_fact_ids"] = list(allocated_fact_ids)
    published = set(b.get("published_fact_ids") or [])
    failed = set(b.get("failed_fact_ids") or [])
    terminal = published | failed
    missing = [x for x in allocated_fact_ids if x not in terminal]
    if not allocated_fact_ids:
        status = "empty"
    elif missing:
        status = "started"
    elif failed:
        status = "partial"
    else:
        status = "completed"
    b["status"] = status
    if status != "started":
        b["completed_at"] = _now()
    return status


def add_run(state: dict, payload: dict) -> None:
    state.setdefault("runs", []).append(dict(payload))
    state["runs"] = state["runs"][-100:]


def get_cached_image(state: dict, fact_id: str) -> dict | None:
    value = state.get("image_cache", {}).get(fact_id)
    return value if isinstance(value, dict) else None


def cache_image(state: dict, fact_id: str, payload: dict) -> None:
    state.setdefault("image_cache", {})[fact_id] = payload
