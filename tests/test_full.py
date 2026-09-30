from __future__ import annotations
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from datetime import date
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

import main
from dataset import facts_for_exact_date, load_all
from state_store import default_state, begin_batch, finalize_batch, mark_fact_failed, mark_fact_published
from scheduler_cursor import SchedulerCursor, target_for_scheduled_run
from test_formatter import test_category_ampersand_is_escaped_once


def test_database_complete_shape():
    facts = load_all()
    assert len(facts) >= 7000, len(facts)
    assert len({f.fact_id for f in facts}) == len(facts)
    assert len(facts_for_exact_date(facts, date(2026, 10, 1))) == 20


def test_retryable_batch_does_not_advance_cursor():
    state = default_state()
    d = date(2026, 10, 1).isoformat()
    cursor = SchedulerCursor(d, d)
    begin_batch(state, d, 1, ["A"])
    mark_fact_failed(state, d, 1, "A", "temporary telegram failure")
    status = finalize_batch(state, d, 1, ["A"])
    assert status == "retryable"
    assert not main.batch_receipt_valid(state, d, 1)
    target, reason = target_for_scheduled_run(state, cursor, 1, date(2026, 10, 2))
    assert target == date(2026, 10, 1) and reason == "ready"


def test_global_claim_deduplication():
    state = default_state()
    d = "2026-10-01"
    begin_batch(state, d, 1, ["A"])
    mark_fact_published(state, d, 1, "A", "claim-x", 123)
    facts = [type("F", (), {"fact_id":"B", "claim_key":"claim-x", "slot":1})()]
    selected = main._pending_from_allocated(facts, state, date(2026,10,2), 10)
    assert selected == []


def run_all():
    test_database_complete_shape()
    test_retryable_batch_does_not_advance_cursor()
    test_global_claim_deduplication()
    test_category_ampersand_is_escaped_once()
    print("full regression tests: PASS")

if __name__ == "__main__":
    run_all()
