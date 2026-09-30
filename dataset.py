from __future__ import annotations

import csv
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

from config import DATA_DIR

EXPECTED_COLUMNS = [
    "ID", "Day", "Date", "Fact", "Slot", "Month", "Title", "Category",
    "Evidence", "Confidence", "Source URL", "Subcategory", "Source title",
    "Source domain", "Verification status",
]
MONTH_FILES = [
    "January.csv", "February.csv", "March.csv", "April.csv", "May.csv", "June.csv",
    "July.csv", "August.csv", "September.csv", "October.csv", "November.csv", "December.csv",
]



def normalize_claim(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").casefold()
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"[^\w\s%.$-]", "", text)
    return text


@dataclass(frozen=True)
class Fact:
    fact_id: str
    day: str
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
    def claim_key(self) -> str:
        raw = normalize_claim(self.fact)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    @property
    def parsed_date(self) -> date:
        return date.fromisoformat(self.date_value)



def _row_to_fact(row: dict[str, str]) -> Fact:
    missing = [c for c in EXPECTED_COLUMNS if c not in row]
    if missing:
        raise ValueError(f"Missing columns: {missing}")
    return Fact(
        fact_id=row["ID"].strip(), day=row["Day"].strip(), date_value=row["Date"].strip(),
        fact=row["Fact"].strip(), slot=int(row["Slot"].strip()), month=row["Month"].strip(),
        title=row["Title"].strip(), category=row["Category"].strip(),
        evidence=row["Evidence"].strip(), confidence=row["Confidence"].strip(),
        source_url=row["Source URL"].strip(), subcategory=row["Subcategory"].strip(),
        source_title=row["Source title"].strip(), source_domain=row["Source domain"].strip(),
        verification_status=row["Verification status"].strip(),
    )



def load_all() -> list[Fact]:
    facts: list[Fact] = []
    for name in MONTH_FILES:
        path = DATA_DIR / name
        if not path.exists():
            raise FileNotFoundError(f"Required database file missing: {path}")
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                facts.append(_row_to_fact(row))
    return facts



def facts_for_exact_date(facts: Iterable[Fact], run_date: date) -> list[Fact]:
    key = run_date.isoformat()
    return sorted((f for f in facts if f.date_value == key), key=lambda f: (f.slot, f.fact_id))



def validate_dataset(facts: list[Fact], strict: bool = False) -> list[str]:
    findings: list[str] = []
    seen_ids: dict[str, int] = {}
    seen_dateslots: dict[tuple[str, int], str] = {}
    for fact in facts:
        seen_ids[fact.fact_id] = seen_ids.get(fact.fact_id, 0) + 1
        key = (fact.date_value, fact.slot)
        prior = seen_dateslots.get(key)
        if prior and prior != fact.fact_id:
            findings.append(f"Duplicate date/slot assignment: {fact.date_value} slot {fact.slot}: {prior} and {fact.fact_id}")
        else:
            seen_dateslots[key] = fact.fact_id
        try:
            date.fromisoformat(fact.date_value)
        except ValueError:
            findings.append(f"Invalid date for {fact.fact_id}: {fact.date_value}")
        if not fact.fact or not fact.title:
            findings.append(f"Missing fact/title: {fact.fact_id}")
        if fact.verification_status.casefold() != "verified":
            findings.append(f"Unverified record: {fact.fact_id} ({fact.verification_status})")
    for fact_id, count in seen_ids.items():
        if count > 1:
            findings.append(f"Duplicate fact ID: {fact_id} x{count}")

    # strict mode additionally requires exactly 20 records for every date present in the DB.
    if strict:
        from collections import Counter
        counts = Counter(f.date_value for f in facts)
        for day_key, count in sorted(counts.items()):
            if count != 20:
                findings.append(f"Expected 20 facts on {day_key}, found {count}")
    return findings
