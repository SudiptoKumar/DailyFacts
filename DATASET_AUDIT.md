# Dataset Audit Snapshot

Source: supplied Daily Facts monthly CSV files.

- Total records: 7,037
- Target: 7,300
- Missing: 263
- July: 456 / 620
- September: 501 / 600
- Exact duplicate claim groups: 254
- All records currently report `Verification status=verified`.
- January-August and September-December currently use mixed years in the `Date` column; runtime scheduling therefore matches month + day only.

The bot contains runtime duplicate-claim protection and never invents filler records.
