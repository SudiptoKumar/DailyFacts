from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import STATE_FILE

SCHEMA_VERSION = 4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_state() -> dict:
    return {"schema_version": SCHEMA_VERSION, "dates": {}, "image_cache": {}, "runs": [], "published_claims": {}}


def _migrate(payload: dict) -> dict:
    state = default_state()
    if not isinstance(payload, dict):
        raise RuntimeError("Publication state must be a JSON object")
    state.update(payload)
    state["schema_version"] = SCHEMA_VERSION
    state.setdefault("dates", {})
    state.setdefault("image_cache", {})
    state.setdefault("runs", [])
    state.setdefault("published_claims", {})
    # Rebuild global claim index from historical published fact records if absent/incomplete.
    for date_key, record in state["dates"].items():
        facts = record.get("facts", {}) if isinstance(record, dict) else {}
        if not isinstance(facts, dict):
            continue
        for fact_id, meta in facts.items():
            if not isinstance(meta, dict) or meta.get("status") != "published":
                continue
            claim_key = str(meta.get("claim_key") or "").strip()
            if claim_key:
                state["published_claims"].setdefault(claim_key, {"date": date_key, "fact_id": fact_id})
    return state


def load_state() -> dict:
    if not STATE_FILE.exists():
        return default_state()
    try:
        payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Publication state JSON is corrupt: {exc}") from exc
    return _migrate(payload)


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_migrate(state), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(STATE_FILE)


def _date_record(state: dict, date_key: str) -> dict:
    dates = state.setdefault("dates", {})
    record = dates.setdefault(date_key, {"batches": {}, "facts": {}})
    record.setdefault("batches", {})
    record.setdefault("facts", {})
    return record


def begin_batch(state: dict, date_key: str, batch: int, allocated_fact_ids: list[str]) -> None:
    record = _date_record(state, date_key)
    old = record["batches"].get(f"batch_{batch}") or {}
    record["batches"][f"batch_{batch}"] = {
        **old,
        "status": "started",
        "allocated_fact_ids": list(allocated_fact_ids),
        "started_at": old.get("started_at") or _now(),
        "published_count": 0,
        "failed_count": 0,
    }


def mark_fact_published(
    state: dict, date_key: str, batch: int, fact_id: str, claim_key: str, message_id: int,
    *, image_page: str = "", image_credit: str = "", image_source: str = "", send_mode: str = "",
) -> None:
    record = _date_record(state, date_key)
    record["facts"][fact_id] = {
        "status": "published", "batch": batch, "claim_key": claim_key, "message_id": int(message_id),
        "published_at": _now(), "image_page": image_page, "image_credit": image_credit,
        "image_source": image_source, "send_mode": send_mode,
    }
    state.setdefault("published_claims", {})[claim_key] = {"date": date_key, "fact_id": fact_id}


def mark_fact_failed(state: dict, date_key: str, batch: int, fact_id: str, reason: str) -> None:
    record = _date_record(state, date_key)
    previous = record["facts"].get(fact_id) or {}
    if previous.get("status") == "published":
        return
    record["facts"][fact_id] = {
        **previous, "status": "failed", "batch": batch, "last_error": str(reason)[:2000], "failed_at": _now()
    }


def is_fact_published_on_date(state: dict, date_key: str, fact_id: str) -> bool:
    record = state.get("dates", {}).get(date_key, {})
    fact = record.get("facts", {}).get(fact_id, {}) if isinstance(record, dict) else {}
    return isinstance(fact, dict) and fact.get("status") == "published"


def is_claim_published_anywhere(state: dict, claim_key: str) -> bool:
    return bool(str(claim_key).strip()) and claim_key in state.get("published_claims", {})


def cache_image(state: dict, fact_id: str, meta: dict) -> None:
    state.setdefault("image_cache", {})[fact_id] = {**meta, "cached_at": _now()}


def get_cached_image(state: dict, fact_id: str) -> dict | None:
    item = state.get("image_cache", {}).get(fact_id)
    return item if isinstance(item, dict) else None


def batch_receipt_valid(state: dict, date_key: str, batch: int) -> bool:
    status = str((state.get("dates", {}).get(date_key, {}).get("batches", {}).get(f"batch_{batch}") or {}).get("status") or "")
    return status in {"completed", "partial", "empty"}


def finalize_batch(state: dict, date_key: str, batch: int, allocated_fact_ids: list[str]) -> str:
    record = _date_record(state, date_key)
    fact_map = record["facts"]
    published = 0
    failed = 0
    for fact_id in allocated_fact_ids:
        status = (fact_map.get(fact_id) or {}).get("status")
        if status == "published":
            published += 1
        elif status == "failed":
            failed += 1
    if not allocated_fact_ids:
        status = "empty"
    elif failed:
        status = "retryable"
    elif published == len(allocated_fact_ids):
        status = "completed"
    else:
        status = "retryable"
    record["batches"][f"batch_{batch}"] = {
        **record["batches"].get(f"batch_{batch}", {}),
        "status": status, "finished_at": _now(), "published_count": published, "failed_count": failed,
    }
    return status


def add_run(state: dict, payload: dict) -> None:
    runs = state.setdefault("runs", [])
    runs.append({**payload, "recorded_at": _now()})
    if len(runs) > 200:
        del runs[:-200]
