from datetime import date
from scheduler_cursor import (
    SchedulerCursor,
    advance_after_terminal,
    initialize_cursor,
    target_for_scheduled_run,
)


def state_for(*records):
    state = {"dates": {}}
    for d, b1, b2 in records:
        state["dates"][d] = {
            "batches": {},
            "facts": {},
        }
        if b1:
            state["dates"][d]["batches"]["batch_1"] = {"status": b1}
        if b2:
            state["dates"][d]["batches"]["batch_2"] = {"status": b2}
    return state


def test_delayed_batch2_crosses_midnight():
    state = state_for(("2026-09-28", "completed", None))
    cursor = initialize_cursor(state, date(2026, 9, 29))
    cursor.next_batch2_date = "2026-09-28"
    target, reason = target_for_scheduled_run(state, cursor, 2, date(2026, 9, 29))
    assert target == date(2026, 9, 28)
    assert reason == "ready"


def test_batch1_catches_the_next_unpublished_calendar_day():
    state = state_for(
        ("2026-09-27", "completed", "completed"),
        ("2026-09-28", "completed", None),
    )
    cursor = initialize_cursor(state, date(2026, 9, 29))
    target, reason = target_for_scheduled_run(state, cursor, 1, date(2026, 9, 29))
    assert target == date(2026, 9, 29)
    assert reason == "ready"


def test_batch2_waits_for_missing_batch1():
    state = state_for(("2026-09-28", None, None))
    cursor = SchedulerCursor("2026-09-28", "2026-09-28")
    target, reason = target_for_scheduled_run(state, cursor, 2, date(2026, 9, 29))
    assert target is None
    assert reason == "waiting_for_batch1"


def test_completed_batch2_moves_forward():
    cursor = SchedulerCursor("2026-09-29", "2026-09-28")
    advance_after_terminal(cursor, 2, date(2026, 9, 28))
    assert cursor.next_batch2_date == "2026-09-29"


def test_future_manual_date_does_not_poison_scheduler():
    state = state_for(("2026-10-10", "completed", "completed"))
    cursor = initialize_cursor(state, date(2026, 9, 29))
    assert cursor.next_batch1_date == "2026-09-29"
    assert cursor.next_batch2_date == "2026-09-29"


def run_all():
    test_delayed_batch2_crosses_midnight()
    test_batch1_catches_the_next_unpublished_calendar_day()
    test_batch2_waits_for_missing_batch1()
    test_completed_batch2_moves_forward()
    test_future_manual_date_does_not_poison_scheduler()
    print("scheduler regression tests: PASS")


if __name__ == "__main__":
    run_all()
