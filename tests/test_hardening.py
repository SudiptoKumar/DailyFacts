from __future__ import annotations
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main
import telegram_client
from scheduler_cursor import SchedulerCursor, target_for_scheduled_run


def test_state_validation_fails_closed_on_malformed_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "publication_state.json"
        path.write_text("{bad json", encoding="utf-8")
        with patch.object(main, "STATE_FILE", path):
            try:
                main._validate_publication_state_file()
            except RuntimeError as exc:
                assert "Publication state is unreadable" in str(exc)
            else:
                raise AssertionError("Malformed publication state was accepted")


def test_scheduler_waits_for_batch1() -> None:
    state = {
        "dates": {
            "2026-09-28": {
                "batches": {"batch_1": {"status": "started"}},
                "facts": {},
            }
        }
    }
    cursor = SchedulerCursor("2026-09-29", "2026-09-28")
    target, reason = target_for_scheduled_run(state, cursor, 2, main.date(2026, 9, 29))
    assert target is None
    assert reason == "waiting_for_batch1"


def test_telegram_5xx_is_not_retried() -> None:
    calls = []

    class Response:
        status_code = 500
        ok = False

        @staticmethod
        def json():
            return {"ok": False, "description": "server error"}

    def post(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()

    fake_settings = SimpleNamespace(telegram_bot_token="token", telegram_channel="@FactsNewsroom")
    with patch.object(telegram_client, "SETTINGS", fake_settings), patch.object(telegram_client.requests, "post", side_effect=post):
        try:
            telegram_client.send_message("hello")
        except RuntimeError:
            pass
        else:
            raise AssertionError("HTTP 5xx should fail without retry")
    assert len(calls) == 1


def test_telegram_429_retries_once() -> None:
    calls = []

    class Response429:
        status_code = 429
        ok = False

        @staticmethod
        def json():
            return {"ok": False, "parameters": {"retry_after": 1}}

    class Response200:
        status_code = 200
        ok = True

        @staticmethod
        def json():
            return {"ok": True, "result": {"message_id": 123}}

    responses = [Response429(), Response200()]

    def post(*args, **kwargs):
        calls.append((args, kwargs))
        return responses.pop(0)

    fake_settings = SimpleNamespace(telegram_bot_token="token", telegram_channel="@FactsNewsroom")
    with patch.object(telegram_client, "SETTINGS", fake_settings), patch.object(telegram_client.requests, "post", side_effect=post), patch.object(telegram_client.time, "sleep") as sleep:
        assert telegram_client.send_message("hello") == 123
        sleep.assert_called_once_with(1)
    assert len(calls) == 2


def run_all() -> None:
    test_state_validation_fails_closed_on_malformed_json()
    test_scheduler_waits_for_batch1()
    test_telegram_5xx_is_not_retried()
    test_telegram_429_retries_once()
    print("hardening tests: PASS")


if __name__ == "__main__":
    run_all()
