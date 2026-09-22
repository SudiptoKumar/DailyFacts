from __future__ import annotations

import argparse
from collections import Counter
from datetime import date

from config import MONTHS
from dataset import duplicate_claim_groups, facts_for_date, load_all


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit Daily Facts dataset")
    parser.add_argument("--date", help="Optional YYYY-MM-DD date to inspect")
    args = parser.parse_args()

    facts = load_all()
    print(f"Total records: {len(facts)}")
    by_month = Counter(f.month for f in facts)
    print("Monthly totals:")
    for number, month in MONTHS.items():
        expected = 28 if number == 2 else (30 if number in {4, 6, 9, 11} else 31)
        expected *= 20
        actual = by_month.get(month, 0)
        print(f"  {month:>9}: {actual:>4} / {expected}")

    groups = duplicate_claim_groups(facts)
    print(f"Exact duplicate claim groups: {len(groups)}")
    for rows in list(groups.values())[:20]:
        print("  " + " | ".join(f.fact_id for f in rows))

    if args.date:
        d = date.fromisoformat(args.date)
        rows = facts_for_date(facts, d.month, d.day)
        print(f"\n{d.isoformat()}: {len(rows)} raw rows")
        seen = set()
        duplicates = []
        for f in rows:
            if f.claim_key in seen:
                duplicates.append(f)
            seen.add(f.claim_key)
        print(f"Unique claims: {len(rows) - len(duplicates)}")
        print("Slots present:", ", ".join(str(f.slot) for f in rows))
        if duplicates:
            print("Duplicate claims on this date:", ", ".join(f.fact_id for f in duplicates))

if __name__ == "__main__":
    main()
