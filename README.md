# Daily Facts V2.14 Fixed

This is a **drop-in hotfix bundle** for the existing Daily Facts repository. It intentionally does not replace the 12 CSV files or the existing image/AI modules.

## Files to replace

```text
main.py
telegram_client.py
scheduler_cursor.py
.github/workflows/daily-facts.yml
```

The bundle also contains:

```text
tests/test_scheduler.py
```

## What this fixes

### 1. Delayed GitHub Actions date bug

The publication date is no longer inferred from the runner's current Asia/Dhaka clock or from the cron firing time. The cron is only used to identify Batch 1 or Batch 2.

A persistent scheduler cursor on the `daily-facts-state` branch decides the next date. Therefore a Batch 2 job that starts after midnight can still publish the previous day's Batch 2 facts.

### 2. Batch 2 no longer jumps to the next day

When Batch 1 is not terminal for the cursor date, Batch 2 performs a safe no-op and keeps its cursor parked. It does not silently switch to the current date.

### 3. Scheduler survives retries

The cursor is restored and committed together with the publication ledger. A successful batch advances only its own cursor.

### 4. Duplicate claims inside a batch no longer deadlock the receipt

Allocation is based on unique claim fingerprints. Duplicate rows are excluded from the terminal batch allocation instead of leaving the batch permanently `started`.

### 5. Corrupt publication state is fail-safe

The bot no longer silently treats malformed JSON publication state as an empty ledger. It stops before publishing, protecting against accidental duplicate posts.

### 6. Telegram duplicate risk reduced

The previous Telegram client retried failed POST requests indiscriminately. A timeout or 5xx can occur after Telegram accepted a message, so blind retries can duplicate a post. V2.14 retries only an explicit HTTP 429 response.

### 7. AI and image failures are isolated

Unexpected editorial-AI exceptions fall back to the verified database title/fact. Unexpected image exceptions fall back to text-only publishing when images are not required.

## Scheduler state

A new file is persisted on `daily-facts-state`:

```text
scheduler_cursor.json
```

It stores:

```json
{
  "schema_version": 1,
  "next_batch1_date": "YYYY-MM-DD",
  "next_batch2_date": "YYYY-MM-DD"
}
```

Do not delete this file.

## Validation performed

The package includes deterministic regression tests for:

- delayed Batch 2 crossing midnight;
- Batch 1 next-date allocation;
- Batch 2 waiting for Batch 1;
- cursor advancement;
- future manual dates not poisoning the scheduler.

The workflow runs these tests before publishing and then runs the existing `python main.py --self-test` suite.

### Important distributed-systems limitation

Telegram Bot API publishing is not an exactly-once transaction with Git state. If the process crashes after Telegram accepts a message but before the local ledger is committed, a duplicate cannot be mathematically ruled out without an external idempotency primitive from Telegram. V2.14 removes the largest avoidable duplication source by eliminating unsafe automatic retries and uses persistent per-fact state plus workflow concurrency.

## Additional hardening

V2.14 also rejects invalid publication-state structure, rejects `--max-posts 0` instead of leaving a batch in a non-terminal state, and avoids repeated duplicate-claim warnings during a single run.

The CI workflow now runs both scheduler regression tests and Telegram/state hardening tests before any production publish step.

### Verified recovery scenarios

The implementation was regression-tested for:

- the observed delayed Batch 2 case: a scheduled `0 11 * * *` run starting after midnight in Bangladesh still targets the previous publication date;
- delayed Batch 1 catch-up using the persistent cursor;
- Batch 2 safe no-op while Batch 1 is not terminal;
- duplicate claim allocation without leaving a batch permanently `started`;
- malformed publication ledger fail-closed behavior;
- Telegram 5xx and transport failures without blind retries;
- Telegram 429 retry exactly once;
- failed fact recovery on the next run;
- manual replay leaving production state unchanged.
