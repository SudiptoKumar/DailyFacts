# Daily Facts Telegram Bot V2.7

**Channel:** https://t.me/FactsNewsroom  
**Bot:** Daily Facts

A dataset-first Telegram bot that publishes verified daily facts from the monthly CSV database. The public post design follows the supplied **Today in History** reference structure while using the Daily Facts branding and free-user-compatible Unicode emoji.

## Daily Publishing

The bot runs **twice per day** in `Asia/Dhaka`:

```text
08:00 → up to 10 posts
17:00 → up to 10 posts
```

The two runs share persistent state. The first run publishes the first available 10 unique facts; the second run continues with the remaining unpublished facts. A fact is never intentionally published twice.

The bot never invents filler facts when the dataset does not contain enough valid records for a date.

## Architecture

```text
12 Monthly CSV Files
        │
        ▼
Dataset Loader + Validation
        │
        ▼
Today's Curated Facts
        │
        ├── Editorial refinement → Cerebras (optional)
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

## Reference-Style Post Format

The public post mirrors the supplied reference structure: image, category line, bold title, concise fact, then the channel footer. The historical date line from the reference is intentionally not used by Daily Facts. The public Source section is removed.

Example:

```text
[RELEVANT IMAGE]

🌌 <b>Space</b>

🪐 <b>A Fact About Neptune</b>

Astronomers mathematically predicted the existence of Neptune, and Johann Gottfried Galle observed the planet in 1846.

<a href="https://t.me/FactsNewsroom"><b>Daily Facts</b></a> #Space #Neptune
```

### Formatting rules

- A category emoji is shown beside the category.
- One locally selected subject emoji is shown before the title.
- Title is bold.
- Fact wording may be AI-refined for grammar, clarity and interest, but its verified meaning must remain unchanged.
- `Daily Facts` is bold, clickable and points to `https://t.me/FactsNewsroom`.
- The public post does **not** display the dataset date.
- Exactly **2 relevant hashtags** appear on the same line as the channel name.
- `#DailyFacts` is not used.
- No public Source section.
- No separate `Why it's interesting` section.
- Emoji are ordinary Unicode emoji, not Telegram custom emoji entities.

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

The scheduler matches **month + day**, not the CSV year.

The supplied database currently contains **7,037 records**, while the intended annual target is **7,300**. The runtime therefore treats missing daily records explicitly instead of manufacturing replacements.

Run:

```bash
python main.py --audit
```

## Image System

The image architecture is adapted from the existing Today in History bot.

### Accuracy-first image matching

The resolver is designed to prefer the **most relevant real image**, not simply the first search result.

The search query is built in this order:

```text
AI-generated visual query (when available)
        ↓
Exact database title
        ↓
Title + subcategory
        ↓
Fact-specific keyword query
```

For every Commons result, the resolver compares:

- title/entity overlap;
- image description/object name;
- Commons categories;
- distinctive fact terms;
- photo/portrait/specimen signals;
- image resolution;
- license availability;
- negative signals such as maps, flags, logos, diagrams, charts, screenshots and collages.

**All candidate queries are ranked together.** The bot does not stop at the first acceptable result. This is the key difference from a simple search-result picker.

The resolver also applies a relevance threshold. When no candidate is sufficiently related to the fact, it does **not** force a random image. The post can safely fall back to text-only instead.

### Exa → Wikimedia fallback

When Commons search misses the subject, Exa is used only to discover additional **Wikimedia Commons** pages. Exa results are also ranked before the image is downloaded. The first Exa result is never blindly trusted.

The use of Wikimedia Commons as the primary image source also keeps image licensing metadata available for internal state tracking. Wikimedia's MediaWiki API exposes file URLs, dimensions and Commons metadata such as descriptions, artist information and license fields.

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

## AI Editorial Layer

### Cerebras

Cerebras is an optional presentation layer. For each fact it can generate:

- a cleaner, more natural title;
- a concise reader-friendly version of the verified fact;
- exactly two topical hashtags;
- a compact `image_query` used only to improve visual search relevance.

The model is explicitly constrained by the database Fact and Evidence. It may improve wording, but it must not invent or change names, dates, numbers, scientific details, causes, comparisons or other factual claims. When Cerebras is unavailable, the original database title and fact are used.

This means the AI improves presentation without becoming the source of truth.

Required when enabled:

```text
CEREBRAS_API_KEY
```

### Exa

Exa is optional at runtime and is primarily used for image-discovery fallback. Contextual Exa enrichment remains disabled by default.

## Telegram Publishing

The bot uses the Telegram Bot API.

Preferred text path:

```text
sendRichMessage (HTML)
```

Photo path:

```text
sendPhoto (HTML caption)
```

Fallback:

```text
sendMessage (HTML)
```

The formatter produces the same visible structure for rich, photo and fallback text delivery.

Bot requirements:

1. Create a bot with `@BotFather`.
2. Add it as an administrator of `@FactsNewsroom`.
3. Give it permission to post messages.
4. Add `TELEGRAM_BOT_TOKEN` to GitHub Actions secrets.

## GitHub Actions

The workflow runs at:

```text
08:00 Asia/Dhaka = 02:00 UTC
17:00 Asia/Dhaka = 11:00 UTC
```

Each scheduled run invokes:

```bash
python main.py --max-posts 10
```

Manual workflow dispatch is available for testing, including a dry-run option.

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

The GitHub workflow runs **twice per day** in Asia/Dhaka:

```text
08:00 → up to 10 pending facts
17:00 → up to 10 remaining pending facts
```

The two runs therefore target 20 facts per day. If fewer than 10 verified, unique records remain for a run, the bot publishes only what is actually available and never invents filler facts.

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
CATCH_UP_MAX=10
STRICT_DATASET=false
IMAGE_REQUIRED=false
CACHE_IMAGES=true
```

## Local Development

Python 3.12 is recommended.

Install:

```bash
python -m pip install -r requirements.txt
```

Run the built-in self-test:

```bash
python main.py --self-test
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
├── emoji_engine.py
├── telegram_client.py
├── state_store.py
├── requirements.txt
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
└── .github/
    └── workflows/
        └── daily-facts.yml
```

## Design Principles

**Database first.** CSV controls what gets published.

**AI for presentation, not truth.** AI cannot invent a replacement fact.

**Accuracy before availability.** Wikimedia Commons is preferred, all candidates are ranked globally, and the bot refuses to attach a weakly related image just to avoid a text-only post.

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
[ ] Run the built-in self-test.
[ ] Run --audit.
[ ] Run a workflow_dispatch dry run.
[ ] Publish one manual test post.
[ ] Enable the scheduled workflow.
```


## V2.7 Image Reliability

The image resolver uses a three-tier strategy:

```text
Fact + cited source page
        ↓
1. Exact source-page image
        ↓ if unavailable / unsuitable
2. Wikimedia Commons multi-query search + global ranking
        ↓
3. Exa discovery restricted to Wikimedia Commons
```

The ranking favors subject/title/entity matches, real-photo signals, relevant source-page imagery and adequate resolution. Logos, avatars, watermarks and generic decorative assets are strongly rejected. Maps, diagrams and illustrations are only treated as softer negatives because some facts are inherently visual and an informative representation can be more relevant than an unrelated stock photograph.

The Telegram publisher uses `sendPhoto` whenever an image is resolved. When an image genuinely cannot be found, it falls back to `sendMessage` with the same multiline HTML structure, so the post never collapses into one continuous line.
