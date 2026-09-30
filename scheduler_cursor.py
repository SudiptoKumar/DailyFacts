from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

VALID_BATCH_RECEIPTS = {"completed", "partial", "empty"}


def _batch_status(state: dict, date_key: str, batch: int) -> str:
    dates = state.get("dates") if isinstance(state, dict) else {}
    record = dates.get(date_key) if isinstance(dates, dict) else None
    batches = record.get("batches") if isinstance(record, dict) else None
    item = batches.get(f"batch_{batch}") if isinstance(batches, dict) else None
    return str(item.get("status") or "unrecorded") if isinstance(item, dict) else "unrecorded"


def batch_receipt_valid(state: dict, date_key: str, batch: int) -> bool:
    return _batch_status(state, date_key, batch) in VALID_BATCH_RECEIPTS


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _safe_date_map(state: dict) -> dict[str, dict]:
    dates = state.get("dates") if isinstance(state, dict) else {}
    return dates if isinstance(dates, dict) else {}


@dataclass
class SchedulerCursor:
    next_batch1_date: str | None = None
    next_batch2_date: str | None = None

    def to_dict(self) -> dict:
        return {
            "schema_version": 1,
            "next_batch1_date": self.next_batch1_date,
            "next_batch2_date": self.next_batch2_date,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "SchedulerCursor":
        return cls(
            next_batch1_date=payload.get("next_batch1_date") if isinstance(payload, dict) else None,
            next_batch2_date=payload.get("next_batch2_date") if isinstance(payload, dict) else None,
        )


def _latest_valid_batch1(state: dict, today: date) -> date | None:
    dates = []
    for key in _safe_date_map(state):
        parsed = _parse_date(key)
        if parsed and parsed <= today and batch_receipt_valid(state, key, 1):
            dates.append(parsed)
    return max(dates) if dates else None


def _earliest_pending_batch2(state: dict, today: date) -> date | None:
    candidates: list[date] = []
    for key in _safe_date_map(state):
        parsed = _parse_date(key)
        if not parsed or parsed > today:
            continue
        if batch_receipt_valid(state, key, 1) and not batch_receipt_valid(state, key, 2):
            candidates.append(parsed)
    return min(candidates) if candidates else None


def initialize_cursor(state: dict, today: date) -> SchedulerCursor:
    """Create a safe cursor when the new sidecar does not exist yet.

    The migration rule deliberately uses the latest known successful Batch 1 date
    rather than replaying months of historical gaps. Batch 2 is initialized to the
    earliest date whose Batch 1 is already terminal but Batch 2 is still pending.
    """
    latest_b1 = _latest_valid_batch1(state, today)
    next_b1 = (latest_b1 + timedelta(days=1)) if latest_b1 else today
    pending_b2 = _earliest_pending_batch2(state, today)
    next_b2 = pending_b2 or today
    return SchedulerCursor(next_b1.isoformat(), next_b2.isoformat())


def refresh_cursor(state: dict, cursor: SchedulerCursor, today: date) -> SchedulerCursor:
    """Advance past already-terminal work without ever jumping over a gap."""
    b1 = _parse_date(cursor.next_batch1_date)
    if b1 is None:
        cursor.next_batch1_date = today.isoformat()
    else:
        while b1 <= today and batch_receipt_valid(state, b1.isoformat(), 1):
            b1 += timedelta(days=1)
        cursor.next_batch1_date = b1.isoformat()

    b2 = _parse_date(cursor.next_batch2_date)
    if b2 is None:
        cursor.next_batch2_date = today.isoformat()
    else:
        while b2 <= today:
            key = b2.isoformat()
            if not batch_receipt_valid(state, key, 1):
                break
            if batch_receipt_valid(state, key, 2):
                b2 += timedelta(days=1)
                continue
            break
        cursor.next_batch2_date = b2.isoformat()
    return cursor


def target_for_scheduled_run(
    state: dict,
    cursor: SchedulerCursor,
    batch: int,
    today: date,
) -> tuple[date | None, str]:
    cursor = refresh_cursor(state, cursor, today)
    raw = cursor.next_batch1_date if batch == 1 else cursor.next_batch2_date
    target = _parse_date(raw)
    if target is None:
        return None, "invalid_cursor"
    if target > today:
        return None, "nothing_due"
    if batch == 2 and not batch_receipt_valid(state, target.isoformat(), 1):
        return None, "waiting_for_batch1"
    return target, "ready"


def advance_after_terminal(cursor: SchedulerCursor, batch: int, run_date: date) -> None:
    key = (run_date + timedelta(days=1)).isoformat()
    if batch == 1:
        cursor.next_batch1_date = key
    else:
        cursor.next_batch2_date = key
