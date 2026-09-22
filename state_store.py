from __future__ import annotations

import json
from datetime import datetime, timezone

from config import STATE_DIR, STATE_FILE


def _fresh_state() -> dict:
    return {"published_fact_ids": {}, "published_claims": {}, "runs": [], "image_cache": {}}


def load_state() -> dict:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        return _fresh_state()
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _fresh_state()
        return {
            "published_fact_ids": data.get("published_fact_ids") if isinstance(data.get("published_fact_ids"), dict) else {},
            "published_claims": data.get("published_claims") if isinstance(data.get("published_claims"), dict) else {},
            "runs": data.get("runs")[-100:] if isinstance(data.get("runs"), list) else [],
            "image_cache": data.get("image_cache") if isinstance(data.get("image_cache"), dict) else {},
        }
    except (OSError, json.JSONDecodeError):
        return _fresh_state()


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    normalized = {
        "published_fact_ids": state.get("published_fact_ids", {}),
        "published_claims": state.get("published_claims", {}),
        "runs": state.get("runs", [])[-100:],
        "image_cache": state.get("image_cache", {}),
    }
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATE_FILE)


def is_published(state: dict, fact_id: str) -> bool:
    return fact_id in state.get("published_fact_ids", {})


def is_claim_published(state: dict, claim_key: str) -> bool:
    return claim_key in state.get("published_claims", {})


def mark_published(state: dict, fact_id: str, claim_key: str, message_id: int | None, *, image_page: str = "", image_credit: str = "", send_mode: str = "", image_source: str = "") -> None:
    published_at = datetime.now(timezone.utc).isoformat()
    state.setdefault("published_fact_ids", {})[fact_id] = {
        "published_at": published_at,
        "message_id": message_id,
        "send_mode": send_mode,
        "image_page": image_page,
        "image_credit": image_credit,
        "image_source": image_source,
    }
    state.setdefault("published_claims", {})[claim_key] = {
        "fact_id": fact_id,
        "published_at": published_at,
    }


def add_run(state: dict, payload: dict) -> None:
    state.setdefault("runs", []).append(payload)
    state["runs"] = state["runs"][-100:]


def get_cached_image(state: dict, fact_id: str) -> dict | None:
    value = state.get("image_cache", {}).get(fact_id)
    return value if isinstance(value, dict) else None


def cache_image(state: dict, fact_id: str, payload: dict) -> None:
    state.setdefault("image_cache", {})[fact_id] = payload
