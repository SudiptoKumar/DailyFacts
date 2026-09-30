# Daily Facts Telegram Bot
### V2.14 Full

> Production-oriented Telegram publisher for **@FactsNewsroom** with an exact-date fact database, deterministic two-batch scheduling, persistent publication state, image relevance checks, optional Cerebras editorial refinement, and GitHub Actions recovery logic.

---

## What this version contains

This is the **complete repository**, not a patch.

It includes:

- the full `data/` database with all 12 monthly CSV files
- the production Python application
- image search and processing
- Cerebras fallback editorial layer
- Telegram publisher
- publication-state handling
- scheduler cursor
- GitHub Actions workflows
- regression tests
- setup documentation
- troubleshooting and recovery documentation

The database supplied with this build contains **7,037 verified records**. The annual target is **7,300 records**, so 263 records are currently missing. The bot does not invent replacement facts.

---

## Public post format

Every normal post follows this structure:

```text
[RELEVANT IMAGE]

🌌 SPACE

🪐 Title

Verified fact text

Daily Facts #RelevantTag #RelevantTag
```

The actual Telegram message uses HTML formatting.

Rules:

- no public date line
- no Source section
- no `Why it's interesting` section
- title is bold
- `Daily Facts` is bold and links to `https://t.me/FactsNewsroom`
- exactly two relevant hashtags
- ordinary Unicode emoji only
- image keeps its native aspect ratio
- no forced crop
- no 3:2 canvas
- no blue or blurred background
- text-only fallback is allowed when no sufficiently relevant image exists

---

# 1. Architecture

```text
12 monthly CSV files
        │
        ▼
Exact-date dataset loader
        │
        ├──────────────► claim/fact duplicate protection
        │
        ▼
Scheduler cursor + publication ledger
        │
        ▼
Editorial layer
  Cerebras optional
  deterministic fallback
        │
        ▼
Image resolver
  Source page
  Wikimedia Commons
  Exa → Wikimedia fallback
        │
        ▼
Image processing
  EXIF correction
  proportional resize
  compression
  no crop
        │
        ▼
Telegram
  sendPhoto + HTML caption
  or sendMessage + HTML
        │
        ▼
Persistent state
  daily-facts-state branch
```

---

# 2. Repository tree

```text
Daily-Facts/
├── main.py
├── config.py
├── dataset.py
├── content_ai.py
├── emoji_engine.py
├── formatter.py
├── image_resolver.py
├── image_pipeline.py
├── scheduler_cursor.py
├── state_store.py
├── telegram_client.py
│
├── requirements.txt
├── .env.example
├── .gitignore
├── VERSION.txt
├── INSTALL.txt
├── README.md
├── DATASET_AUDIT.md
├── TROUBLESHOOTING.md
│
├── data/
│   ├── January.csv
│   ├── February.csv
│   ├── March.csv
│   ├── April.csv
│   ├── May.csv
│   ├── June.csv
│   ├── July.csv
│   ├── August.csv
│   ├── September.csv
│   ├── October.csv
│   ├── November.csv
│   └── December.csv
│
├── state/
│   └── .gitkeep
│
├── generated/
├── logs/
│
├── tests/
│   ├── test_scheduler.py
│   ├── test_hardening.py
│   └── test_full.py
│
└── .github/
    └── workflows/
        ├── daily-facts.yml
        └── test-publish-today.yml
```

---

# 3. Requirements

- GitHub repository
- GitHub Actions enabled
- Python 3.12
- Telegram bot token
- Telegram channel where the bot is an administrator
- Cerebras API key is optional
- Exa API key is optional

No local server is required for scheduled publishing.

---

# 4. Telegram setup

## Step 1: Create the bot

Open Telegram and message **@BotFather**.

Create a new bot and copy the token.

## Step 2: Add the bot to the channel

Open:

`@FactsNewsroom`

Add the bot as an administrator.

The bot must have permission to post messages.

## Step 3: Recommended channel secret

Use:

```text
TELEGRAM_CHANNEL=@FactsNewsroom
```

A numeric channel ID also works if your configuration requires it.

---

# 5. GitHub setup

Push this complete repository to your GitHub repository.

Then open:

```text
Repository
→ Settings
→ Secrets and variables
→ Actions
```

Create these repository secrets.

## Required

```text
TELEGRAM_BOT_TOKEN
```

Value: your BotFather token.

## Strongly recommended

```text
TELEGRAM_CHANNEL
```

Value:

```text
@FactsNewsroom
```

## Optional AI

```text
CEREBRAS_API_KEY
CEREBRAS_MODEL
```

Default model:

```text
gpt-oss-120b
```

## Optional image fallback

```text
EXA_API_KEY
```

The bot still works without Exa because Wikimedia Commons is the primary image source.

---

# 6. Production state branch

The production publication ledger must not live inside the normal source ZIP.

The bot uses a separate branch:

```text
main
  code + data

daily-facts-state
  publication_state.json
  scheduler_cursor.json
```

The state branch protects publication history when the main repository is replaced with a new ZIP.

The workflow automatically creates the branch on first use when it does not already exist.

## Important

Do **not** delete `daily-facts-state` during normal deployments.

Deleting it removes the publication ledger and scheduler cursor and can cause old facts to become publishable again.

---

# 7. Install locally

```bash
python -m venv .venv
```

### Windows

```bash
.venv\Scripts\activate
```

### Linux/macOS

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Copy environment configuration:

```bash
cp .env.example .env
```

Then fill in your credentials.

Never commit `.env`.

---

# 8. Environment configuration

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHANNEL=@FactsNewsroom

CEREBRAS_API_KEY=
CEREBRAS_MODEL=gpt-oss-120b
USE_CEREBRAS=true

EXA_API_KEY=
USE_EXA_CONTEXT=false

TIMEZONE=Asia/Dhaka
POSTS_PER_DAY=20
BATCH_SIZE=10
CATCH_UP_MAX=10

STRICT_DATASET=false
IMAGE_REQUIRED=false
IMAGE_MIN_SCORE=16
CACHE_IMAGES=true

BLOCK_BATCH2_WITHOUT_BATCH1_RECEIPT=true
REQUEST_TIMEOUT=20
IMAGE_TIMEOUT=25
```

---

# 9. Daily schedule

The production schedule is:

```text
08:00 Asia/Dhaka → Batch 1 → slots 01-10
17:00 Asia/Dhaka → Batch 2 → slots 11-20
```

GitHub Actions cron runs in UTC:

```text
02:00 UTC → 08:00 Bangladesh
11:00 UTC → 17:00 Bangladesh
```

The application does **not** use the runner's current Bangladesh date as the source of truth for scheduled publication.

Instead, the persistent scheduler cursor selects the next unpublished calendar date.

That protects against this failure:

```text
Scheduled for:
Sep 28 17:00 Bangladesh

GitHub actually starts:
Sep 29 00:32 Bangladesh

OLD BEHAVIOR:
Use Sep 29 → wrong date

V2.14:
Read scheduler cursor → Sep 28
```

This is the key scheduler reliability change.

---

# 10. Batch behavior

## Batch 1

```text
Slots 01-10
```

## Batch 2

```text
Slots 11-20
```

Batch 2 will not silently select slots 01-10 again.

Batch 2 also waits when there is no terminal Batch 1 receipt for the same date.

Example:

```text
Batch 1 failed
      ↓
Batch 2
      ↓
SAFE NO-OP
      ↓
Retry Batch 1 later
```

---

# 11. Failure recovery

A publication batch can end with these states:

```text
completed
empty
retryable
```

`retryable` is important.

A failed Telegram publication does **not** advance the scheduler cursor.

The next scheduled run can retry the failed fact.

This prevents the system from silently skipping a fact after a transient failure.

---

# 12. Telegram retry policy

Telegram calls are deliberately conservative.

### HTTP 429

The server explicitly says the request was rate-limited.

The bot waits for `retry_after` and performs one retry.

### Network timeout / connection failure

The bot does **not** blindly retry a non-idempotent post.

Reason:

```text
Telegram may have accepted the message
but the client may have lost the response.
```

Blind retrying can create duplicates.

The failed fact becomes retryable through the publication ledger.

### HTTP 5xx

The post fails without an unsafe blind retry.

---

# 13. Duplicate protection

There are multiple layers.

## Fact ID

A published fact ID cannot be published twice for the same date.

## Claim fingerprint

The normalized fact statement is hashed.

A repeated claim under another ID is also blocked after it has already been published.

## Batch allocation

The same claim cannot occupy multiple slots inside the same allocated batch.

## Persistent state

Publication state is stored separately from source code.

---

# 14. Image system

Image selection is relevance-first.

Order:

```text
1. Source-page image
2. Wikimedia Commons multi-query search
3. Exa discovery restricted to Wikimedia Commons
```

The resolver ranks candidates instead of blindly taking the first result.

Negative signals include:

```text
logo
icon
flag
map
screenshot
poster
collage
watermark
avatar
```

Low-resolution or obviously unrelated candidates are rejected.

When no sufficiently relevant image is found:

```text
No acceptable image
        ↓
Text-only Telegram post
```

No random image is attached merely to avoid a text-only post.

---

# 15. Image dimensions

The image pipeline:

```text
Download
   ↓
EXIF orientation
   ↓
Proportional resize when oversized
   ↓
JPEG compression if needed
   ↓
Telegram
```

It does not:

- crop
- force 3:2
- add blue side panels
- add blurred backgrounds
- add white canvas padding
- stretch the image

The original aspect ratio is preserved.

---

# 16. Cerebras behavior

Cerebras is an editorial layer only.

It can refine:

- title wording
- fact wording
- two hashtags
- image search query

It cannot replace the database fact.

When Cerebras returns:

- 429
- timeout
- malformed JSON
- unusable output
- API error

the bot immediately uses the deterministic database title/fact fallback.

Publishing does not stop because AI is unavailable.

---

# 17. Database behavior

The database is the source of truth.

Required files:

```text
data/January.csv
...
data/December.csv
```

Required columns:

```text
ID
Day
Date
Fact
Slot
Month
Title
Category
Evidence
Confidence
Source URL
Subcategory
Source title
Source domain
Verification status
```

The application matches the exact `Date` value.

It does not use month/day guessing across different dates.

It never pulls a fact from the next or previous date just to reach 20 posts.

---

# 18. Current database audit

This supplied database contains:

```text
7,037 verified records
```

Target:

```text
365 days × 20 facts = 7,300
```

Current shortage:

```text
263 records
```

The shortage is currently concentrated in:

```text
September 2026: 501 / 600
July 2027:       456 / 620
```

The bot therefore publishes fewer than 20 posts on dates where the database itself has fewer valid records.

This is intentional. It is safer than inventing facts.

See `DATASET_AUDIT.md` for the exact affected dates.

---

# 19. Local commands

## Compile all Python files

```bash
python -m py_compile *.py tests/*.py
```

## Run the complete application self-test

```bash
python main.py --self-test
```

## Audit the database

```bash
python main.py --audit
```

## Preview a date

```bash
python main.py --preview --date 2026-10-01 --batch 1
```

## Dry-run without Telegram publishing

```bash
python main.py --dry-run --date 2026-10-01 --batch 1 --max-posts 10 --no-image
```

## Scheduler regression tests

```bash
python tests/test_scheduler.py
```

## Hardening tests

```bash
python tests/test_hardening.py
```

## Full regression tests

```bash
python tests/test_full.py
```

---

# 20. Manual production test

Use GitHub Actions:

```text
Actions
→ Daily Facts
→ Run workflow
```

Recommended first test:

```text
Date: 2026-10-01
Batch: 1
Max posts: 1
Dry run: true
```

Then test one real post only after the dry run passes.

---

# 21. Visual replay test

The repository contains:

```text
.github/workflows/test-publish-today.yml
```

This workflow is manual only.

It intentionally ignores production publication state and does not update `daily-facts-state`.

Use it when you want to inspect the current Telegram rendering and image resolver again.

This is a test tool, not the normal scheduler.

---

# 22. State recovery

If the bot reports:

```text
Publication state is unreadable
```

do not delete the state branch.

The correct recovery process is:

1. Stop scheduled publication.
2. Inspect `publication_state.json` on `daily-facts-state`.
3. Repair the JSON if it is a manual corruption.
4. Re-run the self-test.
5. Re-enable scheduling.

The application intentionally fails closed rather than replacing a corrupt ledger with an empty one.

---

# 23. Deployment rule

For a normal code/data replacement:

```text
Replace main branch source/data
        ↓
Keep daily-facts-state
        ↓
Run tests
        ↓
Enable schedule
```

Do not package the runtime publication ledger into the production source ZIP.

---

# 24. What this version specifically prevents

```text
✓ Batch 1 publishing the same first 10 facts twice
✓ Batch 2 silently switching to the next calendar day after midnight
✓ Batch 2 running before Batch 1 has a terminal receipt
✓ Corrupt publication state being silently replaced
✓ Unsafe automatic retries after Telegram transport/5xx failures
✓ Endless 429 retry loops
✓ Failed facts permanently advancing the scheduler cursor
✓ Random unrelated images being forced onto posts
✓ Image aspect-ratio distortion
✓ AI outage stopping publication
✓ Manual replay modifying production publication state
✓ ZIP replacement deleting normal publication history
```

---

# 25. Important Telegram idempotency limitation

There is one platform-level limitation that cannot be removed by local code alone.

Telegram does not expose a transaction that atomically combines:

```text
send channel message
+
commit publication state
```

Therefore this theoretical sequence remains possible:

```text
Telegram accepts message
        ↓
process crashes
        ↓
state commit never happens
```

A later retry could produce a duplicate.

V2.14 avoids the common avoidable duplication paths and deliberately avoids blind retries, but no local Git-based state file can create an atomic transaction with Telegram.

---

# 26. Production checklist

Before enabling scheduled publication:

```text
[ ] Database files are present
[ ] TELEGRAM_BOT_TOKEN configured
[ ] TELEGRAM_CHANNEL configured
[ ] Bot is administrator of @FactsNewsroom
[ ] daily-facts-state branch retained
[ ] python -m py_compile passes
[ ] test_scheduler.py passes
[ ] test_hardening.py passes
[ ] test_full.py passes
[ ] python main.py --self-test passes
[ ] Manual dry-run passes
[ ] One real manual post looks correct
[ ] Scheduled workflow enabled
```

---

# 27. Version

```text
Daily Facts V2.14 Full
```

This build combines the scheduler/hardening changes with the complete supplied database and a complete runnable repository.
