from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["CEREBRAS_API_KEY"] = "test-key-1"
os.environ["CEREBRAS_API_KEY_2"] = "test-key-2"

import requests

import cerebras_keys
import content_ai
from config import SETTINGS
from dataset import Fact

KEY1, KEY2 = "test-key-1", "test-key-2"
AI_BODY = {"choices": [{"message": {"content": '{"title": "AI Title", "fact": "An AI refined fact that is long enough to use.", "hashtags": ["Ocean"], "image_query": "ocean waves"}'}}]}


def _fact() -> Fact:
    return Fact(
        fact_id="TEST-1", day="01", date_value="2026-10-01",
        fact="A verified test fact containing an ampersand & symbol.",
        slot=1, month="October", title="Test title", category="Ocean & Marine",
        evidence="Verified test evidence.", confidence="high",
        source_url="https://example.com", subcategory="Test",
        source_title="Example", source_domain="example.com",
        verification_status="Verified",
    )


class FakeResponse:
    def __init__(self, status: int):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self):
        return AI_BODY


class FakeTransport:
    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.cerebras_auth = []
        self.alerts = []

    def __call__(self, url, **kwargs):
        if "api.telegram.org" in url:
            self.alerts.append(kwargs.get("data", {}).get("text", ""))
            return FakeResponse(200)
        key = (kwargs.get("headers") or {}).get("Authorization", "").replace("Bearer ", "")
        self.cerebras_auth.append(key)
        statuses = self.script.get(key) or [200]
        return FakeResponse(statuses.pop(0) if len(statuses) > 1 else statuses[0])


def _run(script):
    transport = FakeTransport(script)
    original = requests.post
    requests.post = transport
    try:
        return content_ai.enrich_fact(_fact()), transport
    finally:
        requests.post = original


def test_failover_to_second_key() -> None:
    cerebras_keys._reset_state()
    result, transport = _run({KEY1: [402], KEY2: [200]})
    assert result.title == "AI Title", result
    assert transport.cerebras_auth == [KEY1, KEY2], transport.cerebras_auth
    assert SETTINGS.cerebras_api_key == KEY2


def test_non_quota_error_uses_fallback_without_rotating() -> None:
    cerebras_keys._reset_state()
    result, transport = _run({KEY1: [400], KEY2: [200]})
    assert result.title == "Test title", result
    assert transport.cerebras_auth == [KEY1], transport.cerebras_auth
    assert SETTINGS.cerebras_api_key == KEY1


def test_both_keys_dead_uses_fallback() -> None:
    cerebras_keys._reset_state()
    result, transport = _run({KEY1: [402], KEY2: [402]})
    assert result.title == "Test title", result
    assert transport.cerebras_auth == [KEY1, KEY2], transport.cerebras_auth


def run_all() -> None:
    test_failover_to_second_key()
    test_non_quota_error_uses_fallback_without_rotating()
    test_both_keys_dead_uses_fallback()
    cerebras_keys._reset_state()
    print("keyring failover tests: PASS")


if __name__ == "__main__":
    run_all()
