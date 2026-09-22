from __future__ import annotations

import csv
import hashlib
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from config import DATA_DIR, KNOWN_CATEGORIES, MONTHS, REQUIRED_COLUMNS


@dataclass(frozen=True)
class Fact:
    fact_id: str
    day: int
    date_value: str
    fact: str
    slot: int
    month: str
    title: str
    category: str
    evidence: str
    confidence: str
    source_url: str
    subcategory: str
    source_title: str
    source_domain: str
    verification_status: str

    @property
    def month_number(self) -> int:
        for number, name in MONTHS.items():
            if name.lower() == self.month.lower():
                return number
        return 0

    @property
    def claim_key(self) -> str:
        return claim_fingerprint(self.fact)

    def to_dict(self) -> dict:
        return asdict(self)


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _int(value: str, field: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        raise ValueError(f"Invalid {field}: {value!r}")


def claim_fingerprint(value: str) -> str:
    # Exact semantic normalization, intentionally conservative. It catches repeated
    # claims with punctuation/case/whitespace changes without attempting unsafe NLP.
    text = (value or "").lower()
    text = text.replace("’", "'").replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def load_month(path: Path) -> list[Fact]:
    out: list[Fact] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - fields
        if missing:
            raise ValueError(f"{path.name}: missing columns: {', '.join(sorted(missing))}")
        for row in reader:
            month = _clean(row.get("Month")) or path.stem
            out.append(Fact(
                fact_id=_clean(row.get("ID")),
                day=_int(row.get("Day", ""), "Day"),
                date_value=_clean(row.get("Date")),
                fact=_clean(row.get("Fact")),
                slot=_int(row.get("Slot", ""), "Slot"),
                month=month,
                title=_clean(row.get("Title")),
                category=_clean(row.get("Category")),
                evidence=_clean(row.get("Evidence")),
                confidence=_clean(row.get("Confidence")),
                source_url=_clean(row.get("Source URL")),
                subcategory=_clean(row.get("Subcategory")),
                source_title=_clean(row.get("Source title")),
                source_domain=_clean(row.get("Source domain")),
                verification_status=_clean(row.get("Verification status")),
            ))
    return out


def load_all() -> list[Fact]:
    facts: list[Fact] = []
    for number in range(1, 13):
        path = DATA_DIR / f"{MONTHS[number]}.csv"
        if path.exists():
            facts.extend(load_month(path))
    return facts


def facts_for_date(facts: Iterable[Fact], month: int, day: int) -> list[Fact]:
    month_name = MONTHS[month]
    matches = [f for f in facts if f.month.lower() == month_name.lower() and f.day == day]
    # Respect curated slot order. Never reorder the editorial database by score.
    matches.sort(key=lambda f: (f.slot, f.fact_id))
    return matches


def duplicate_claim_groups(facts: Iterable[Fact]) -> dict[str, list[Fact]]:
    groups: dict[str, list[Fact]] = defaultdict(list)
    for fact in facts:
        if fact.fact:
            groups[fact.claim_key].append(fact)
    return {key: rows for key, rows in groups.items() if len(rows) > 1}


def validate_dataset(facts: list[Fact], *, strict: bool = False) -> list[str]:
    errors: list[str] = []
    ids: set[str] = set()
    date_slots: set[tuple[str, int, int]] = set()
    date_counts: dict[tuple[str, int], int] = defaultdict(int)

    for f in facts:
        if not f.fact_id:
            errors.append("Missing ID")
        elif f.fact_id in ids:
            errors.append(f"Duplicate ID: {f.fact_id}")
        ids.add(f.fact_id)
        if not f.fact or not f.title:
            errors.append(f"{f.fact_id}: missing Fact or Title")
        if not 1 <= f.day <= 31:
            errors.append(f"{f.fact_id}: invalid day {f.day}")
        if not 1 <= f.slot <= 20:
            errors.append(f"{f.fact_id}: invalid slot {f.slot}")
        key = (f.month.lower(), f.day, f.slot)
        if key in date_slots:
            errors.append(f"Duplicate Month/Day/Slot: {f.month} {f.day} slot {f.slot}")
        date_slots.add(key)
        date_counts[(f.month.lower(), f.day)] += 1
        if f.category not in KNOWN_CATEGORIES:
            errors.append(f"{f.fact_id}: unknown category {f.category!r}")
        if f.verification_status.lower() != "verified":
            errors.append(f"{f.fact_id}: verification status is {f.verification_status!r}")
        parsed = urlparse(f.source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append(f"{f.fact_id}: invalid Source URL")

    duplicate_groups = duplicate_claim_groups(facts)
    if duplicate_groups:
        errors.append(f"Duplicate fact claims detected: {len(duplicate_groups)} groups")
        if strict:
            for rows in list(duplicate_groups.values())[:30]:
                errors.append("  " + " | ".join(f.fact_id for f in rows))

    # Date completeness is reported but not automatically treated as fatal unless strict.
    for month_num, month_name in MONTHS.items():
        max_day = 28 if month_num == 2 else (30 if month_num in {4, 6, 9, 11} else 31)
        for day in range(1, max_day + 1):
            count = date_counts.get((month_name.lower(), day), 0)
            if count != 20:
                errors.append(f"{month_name} {day}: expected 20 facts, found {count}")

    if strict:
        return errors
    # In non-strict mode only structural/data-corruption errors are fatal to callers.
    fatal_prefixes = (
        "Missing ID", "Duplicate ID:", "Duplicate Month/Day/Slot:", "missing Fact or Title",
        "invalid day", "invalid slot", "unknown category", "verification status is", "invalid Source URL",
    )
    return [e for e in errors if e.startswith(fatal_prefixes)]


def daily_quality(facts: list[Fact], month: int, day: int) -> dict:
    rows = facts_for_date(facts, month, day)
    seen: set[str] = set()
    unique: list[Fact] = []
    duplicates: list[Fact] = []
    for f in rows:
        if f.claim_key in seen:
            duplicates.append(f)
        else:
            seen.add(f.claim_key)
            unique.append(f)
    return {
        "raw_count": len(rows),
        "unique_count": len(unique),
        "duplicate_count": len(duplicates),
        "missing_slots": [slot for slot in range(1, 21) if not any(f.slot == slot for f in rows)],
        "duplicates": [f.fact_id for f in duplicates],
    }
