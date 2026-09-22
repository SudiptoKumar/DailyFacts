# Daily Facts Telegram Bot V1

**Channel:** https://t.me/FactsNewsroom  
**Bot:** Daily Facts

A dataset-first Telegram bot that publishes **20 unique facts per day** from 12 monthly CSV files. Each fact is formatted into a consistent Daily Facts post, enriched only for presentation, matched with a relevant real image, and published with duplicate protection and retry/fallback handling.

## Architecture

```text
12 Monthly CSV Files
        │
        ▼
Dataset Loader + Validation
        │
        ▼
Today's 20 Curated Slots
        │
        ├── Presentation → Cerebras (optional)
        │
        └── Image → Wikimedia Commons → Exa fallback
                         │
                         ▼
                 Image Processing
                         │
                         ▼
                  Telegram Formatter
                         │
                         ▼
                   @FactsNewsroom
                         │
                         ▼
                   Persistent State
```

## Post Format

```text
[Relevant photo]

🧠/🔬/🌌 CATEGORY

Title

Verified fact.

💡 Why it's interesting
Short contextual sentence.

#Topic #Subtopic #DailyFacts
```

The public post does not expose internal `Evidence`, `Confidence`, or `Verification status` fields.

## Data

The bot reads:

```text
data/January.csv
...
data/December.csv
```

Required columns:

```text
ID, Day, Date, Fact, Slot, Month, Title, Category,
Evidence, Confidence, Source URL, Subcategory,
Source title, Source domain, Verification status
```

The scheduler matches **month + day**, not the CSV year. This is intentional because the supplied annual dataset currently mixes publication years in its `Date` column.

### Current dataset audit

The supplied dataset contains **7,037 records**.

Expected annual target:

```text
365 × 20 = 7,300
```

Current gaps:

```text
July       456 / 620
September  501 / 600
```

The dataset also contains exact duplicate-claim groups. The runtime deduplicator prevents the same normalized claim from being published twice, including across different dates.

The bot never invents filler facts to compensate for missing records.

Run:

```bash
python main.py --audit
```

## Image System

The image architecture is adapted from the existing Today in History bot.

### Primary: Wikimedia Commons

The resolver creates contextual queries from:

```text
Title
Category
Subcategory
Fact subject
```

Candidates are scored using textual relevance, category/subcategory overlap, title overlap, image size, and generic-image penalties.

The resolver rejects weak candidates such as unrelated logos, flags, icons, maps, symbols, and very small images.

### Fallback: Exa → Wikimedia

When direct Commons search cannot find an acceptable image, Exa can discover a relevant Wikimedia Commons page. The bot then extracts the page image metadata and downloads the actual image.

### Image processing

```text
Download
  ↓
Validate image
  ↓
EXIF orientation
  ↓
Preserve aspect ratio
  ↓
Resize only when needed
  ↓
JPEG compression if needed
  ↓
Telegram
```

**No cropping is performed.**

## AI Layer

### Cerebras

Cerebras is used only to improve presentation:

- concise title
- readable fact wording
- one `Why it's interesting` sentence
- two topical hashtags + `#DailyFacts`

The CSV fact remains authoritative. The model is instructed not to introduce unsupported factual information.

Required:

```text
CEREBRAS_API_KEY
```

### Exa

Exa is optional at runtime.

It is used for image discovery fallback. Optional contextual enrichment is disabled by default because the database already contains verified fact text and sources.

Set:

```text
EXA_API_KEY
```

and enable `USE_EXA_CONTEXT=true` only when needed.

## Telegram Publishing

The bot uses the Telegram Bot API.

Preferred path:

```text
sendRichMessage
```

Fallback:

```text
sendPhoto / sendMessage
```

The current Telegram Bot API supports Rich Messages and the `sendRichMessage` method. If the rich-message path fails, the bot falls back to the standard Bot API path so one formatting incompatibility does not stop publishing. 

Bot requirements:

1. Create a bot with `@BotFather`.
2. Add it as an administrator of `@FactsNewsroom`.
3. Give it permission to post messages.
4. Add the token as the GitHub Actions secret `TELEGRAM_BOT_TOKEN`.

## State and Duplicate Protection

State:

```text
state/posted_state.json
```

The bot records:

- Fact ID
- Normalized claim fingerprint
- Telegram message ID
- Publication time
- send mode
- image source/page/credit
- run history

This provides two levels of protection:

```text
Fact ID duplicate protection
        +
Claim duplicate protection
```

A repeated claim is therefore skipped even if it appears under a different ID or date.

## Scheduling

The GitHub workflow runs hourly in the publication window:

```text
08:00 → 1st pending fact
09:00 → 2nd pending fact
...
03:00 → 20th pending fact
```

The workflow cron uses UTC:

```text
02:00–21:00 UTC
```

which corresponds to the planned `Asia/Dhaka` window.

The bot always chooses the **next unpublished slot** rather than trusting the exact clock slot. This means a delayed or failed workflow run does not cause the same fact to be skipped or reordered.

## Configuration

Copy:

```bash
cp .env.example .env
```

Core settings:

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHANNEL=@FactsNewsroom
EXA_API_KEY=
CEREBRAS_API_KEY=
CEREBRAS_MODEL=gpt-oss-120b
USE_CEREBRAS=true
USE_EXA_CONTEXT=false
TIMEZONE=Asia/Dhaka
POSTS_PER_DAY=20
CATCH_UP_MAX=1
STRICT_DATASET=false
IMAGE_REQUIRED=false
CACHE_IMAGES=true
```

## Local Development

Python 3.12 is recommended.

Install:

```bash
python -m pip install -r requirements-dev.txt
```

Run tests:

```bash
pytest -q
```

Compile source:

```bash
python -m py_compile main.py *.py
```

Audit the complete dataset:

```bash
python main.py --audit
```

Preview today's pending records:

```bash
python main.py --preview
```

Dry-run one date without Telegram publishing:

```bash
python main.py --dry-run --date 2026-10-01 --max-posts 1 --no-image
```

## Production Run

The normal command is:

```bash
python main.py
```

For a specific date:

```bash
python main.py --date 2026-10-01 --max-posts 1
```

For a local no-network smoke test:

```bash
python main.py --self-test
```

## Failure Handling

Each fact is isolated.

```text
Image failure
   ↓
Try fallback image resolver
   ↓
No image
   ↓
Text-only Rich Message
   ↓
Rich Message failure
   ↓
Standard Telegram API fallback
```

A failed fact does not stop the remaining workflow.

State is saved after each successful publication.

## Project Tree

```text
Daily-Facts/
├── main.py
├── config.py
├── dataset.py
├── content_ai.py
├── image_resolver.py
├── image_pipeline.py
├── formatter.py
├── telegram_client.py
├── state_store.py
├── audit.py
├── requirements.txt
├── requirements-dev.txt
├── .env.example
├── README.md
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
│   └── posted_state.json
│
├── generated/
├── logs/
│
├── tests/
│   ├── test_core.py
│   └── test_images.py
│
└── .github/
    └── workflows/
        └── daily-facts.yml
```

## Design Principles

**Database first.** CSV controls what gets published.

**AI for presentation, not truth.** AI cannot invent a replacement fact.

**Real images first.** Wikimedia Commons is preferred, with Exa as discovery fallback.

**No duplicate knowledge.** Fact IDs and claim fingerprints are both tracked.

**No filler.** Missing records are reported instead of fabricated.

**Failure isolation.** One broken fact/image/API call must not terminate the whole process.

**Deterministic order.** The `Slot` field controls daily editorial order.

## Production Readiness Checklist

Before enabling the schedule:

```text
[ ] Complete the missing July facts.
[ ] Complete the missing September facts.
[ ] Resolve duplicate fact claims.
[ ] Add TELEGRAM_BOT_TOKEN GitHub secret.
[ ] Add CEREBRAS_API_KEY GitHub secret.
[ ] Add EXA_API_KEY GitHub secret if image fallback is required.
[ ] Confirm the bot is admin in @FactsNewsroom.
[ ] Run pytest -q.
[ ] Run --audit.
[ ] Run a workflow_dispatch dry run.
[ ] Publish one manual test post.
[ ] Enable the scheduled workflow.
```
