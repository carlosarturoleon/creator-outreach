# Windsor.ai YouTube Influencer Finder - LangGraph Agent

## Context
Build a LangGraph-based AI agent pipeline from scratch in an empty Python project. The agent searches YouTube for relevant influencers, enriches their channel data, filters and scores them for Windsor.ai affiliate program fit, and generates personalized outreach emails. Windsor.ai is a no-code marketing data integration/attribution SaaS with a 30% recurring affiliate program — target audience is digital marketers, analytics practitioners, and performance marketing content creators.

A SQLite database (`output/influencers.db`) acts as the persistence layer for the full pipeline. Every retrieved channel is persisted (raw, enriched, scored, emailed), and channels already emailed are skipped on future runs to avoid duplicate outreach.

---

## Project File Structure

```
influencers-finder/
├── CLAUDE.md
├── PLAN.md                       # This file
├── .env                          # API keys (gitignored)
├── .env.example
├── .gitignore
├── requirements.txt
├── main.py                       # CLI entrypoint
└── src/
    ├── __init__.py
    ├── state.py                  # GraphState TypedDict
    ├── config.py                 # Dotenv settings loader
    ├── graph.py                  # StateGraph assembly + compile()
    ├── nodes/
    │   ├── __init__.py
    │   ├── search_channels.py    # Node 1: YouTube search.list
    │   ├── deduplicate_vs_db.py  # Node 1.5: skip already-emailed channels
    │   ├── enrich_channel_data.py # Node 2: channels.list + playlistItems + videos.list
    │   ├── filter_influencers.py  # Node 3: hard + soft filters
    │   ├── score_influencers.py   # Node 4: composite score + Claude relevance
    │   ├── generate_emails.py     # Node 5: Claude structured email generation
    │   └── save_results.py        # Node 6: CSV + JSON output
    ├── tools/
    │   ├── __init__.py
    │   ├── youtube_client.py      # YouTube Data API v3 wrapper
    │   └── llm_client.py          # ChatAnthropic factory
    ├── models/
    │   ├── __init__.py
    │   ├── channel.py             # ChannelData Pydantic model
    │   ├── scored_influencer.py   # ScoringResult Pydantic model
    │   └── outreach_email.py      # EmailResult Pydantic model
    └── db/
        ├── __init__.py
        └── database.py            # SQLite persistence layer
```

---

## Graph Architecture

```
START → search_channels → deduplicate_vs_db → enrich_channel_data → filter_influencers
                                    |                                        |
                               [all emailed]                        [conditional edge]
                                    |                               /                 \
                                   END                    [has results]           [empty] → END
                                                                |
                                                        score_influencers → generate_emails → save_results → END
```

Every node that produces data also writes to SQLite immediately (upsert). `deduplicate_vs_db` queries the DB for already-emailed channels and removes them from `raw_channels` before enrichment begins.

---

## State Schema (`src/state.py`)

`GraphState` TypedDict with `Annotated[list, operator.add]` reducers on all list fields (safe append semantics, enables future parallelism):

| Field | Type | Description |
|---|---|---|
| `search_keywords` | `list[str]` | Input keywords |
| `min_subscribers` | `int` | Filter threshold |
| `min_engagement_rate` | `float` | Filter threshold (%) |
| `target_languages` | `list[str]` | e.g. `["en"]` |
| `max_results_per_keyword` | `int` | YouTube search limit |
| `raw_channels` | `Annotated[list, operator.add]` | After search node |
| `enriched_channels` | `Annotated[list, operator.add]` | After enrichment node |
| `filtered_channels` | `Annotated[list, operator.add]` | After filter node |
| `scored_influencers` | `Annotated[list, operator.add]` | After scoring node |
| `outreach_emails` | `Annotated[list, operator.add]` | After email node |
| `error_log` | `Annotated[list, operator.add]` | Non-fatal errors |
| `current_phase` | `str` | Debug/logging |
| `skipped_channel_ids` | `Annotated[list, operator.add]` | IDs skipped (already emailed) |

---

## SQLite Database (`src/db/database.py`)

Single file with a `Database` class managing one connection to `output/influencers.db`.

### Tables

```sql
-- All channels ever retrieved (upserted on each run)
CREATE TABLE channels (
    channel_id TEXT PRIMARY KEY,
    channel_title TEXT,
    description TEXT,
    subscriber_count INTEGER,
    total_view_count INTEGER,
    video_count INTEGER,
    country TEXT,
    default_language TEXT,
    keywords TEXT,            -- JSON array
    avg_views_per_video REAL,
    avg_likes_per_video REAL,
    avg_comments_per_video REAL,
    engagement_rate REAL,
    upload_frequency_days REAL,
    recent_video_titles TEXT, -- JSON array
    search_keyword TEXT,
    first_seen_at TEXT,
    last_updated_at TEXT
);

-- Scoring results per channel
CREATE TABLE scored_influencers (
    channel_id TEXT PRIMARY KEY REFERENCES channels(channel_id),
    composite_score REAL,
    engagement_score REAL,
    audience_size_score REAL,
    relevance_score REAL,
    relevance_rationale TEXT,
    niche_tags TEXT,          -- JSON array
    scored_at TEXT
);

-- Generated outreach emails
CREATE TABLE outreach_emails (
    channel_id TEXT PRIMARY KEY REFERENCES channels(channel_id),
    subject_line TEXT,
    email_body TEXT,
    personalization_hooks TEXT, -- JSON array
    generated_at TEXT,
    sent_at TEXT                -- NULL until manually marked sent
);
```

### Key methods
- `init_db()` — creates tables if not exist
- `get_emailed_channel_ids() -> set[str]` — returns all channel_ids with a row in `outreach_emails`
- `upsert_channel(channel_data: dict)` — insert or update channel row
- `upsert_scored_influencer(scored: dict)` — insert or update score row
- `upsert_email(email: dict)` — insert or update email row

---

## Node Breakdown

### Node 1: `search_channels`
- Calls `search.list` (100 quota units/call) per keyword
- Deduplicates by `channel_id` across keywords
- Returns: `raw_channels`

### Node 1.5: `deduplicate_vs_db`
- Calls `db.get_emailed_channel_ids()` to get already-emailed channel IDs
- Removes matching IDs from `raw_channels`, logs to `skipped_channel_ids`
- Conditional edge: if `raw_channels` is empty after dedup → `END`

### Node 2: `enrich_channel_data`
- **Phase A**: Batched `channels.list` → subscribers, views, country, language, keywords
- **Phase B**: Per-channel: `contentDetails` → uploads playlist → `playlistItems.list` → `videos.list` → engagement stats
- Computes: `engagement_rate = (avg_likes + avg_comments) / avg_views * 100`
- Persists each channel via `db.upsert_channel()`

### Node 3: `filter_influencers`
- Hard filter 1: `subscriber_count >= min_subscribers`
- Hard filter 2: `engagement_rate >= min_engagement_rate`
- Hard filter 3: language (permissive — only blocks confirmed mismatches)
- Soft filter 4: Windsor.ai niche keyword match (description + keywords + video titles)
- Conditional edge: if `filtered_channels` is empty → `END`

### Node 4: `score_influencers`
- **Engagement score** (0–40 pts): log scale `min(40, 40 * log(1+rate) / log(11))`
- **Audience size score** (0–30 pts): tiered thresholds
- **Relevance score** (0–30 pts): Claude `with_structured_output(ScoringResult)` at temp=0.2
- Sorts by `composite_score` descending; persists via `db.upsert_scored_influencer()`

### Node 5: `generate_emails`
- Claude `with_structured_output(EmailResult)` at temp=0.7
- Prompt: genuine video title reference, Windsor.ai pitch, 30% commission offer, soft CTA, ≤200 words
- Persists via `db.upsert_email()` (`sent_at` = NULL until manually updated)

### Node 6: `save_results`
- Writes `output/influencers_YYYYMMDD_HHMMSS.csv` and `.json`
- Prints pipeline summary to stdout

---

## Scoring Formula

```
Composite (0-100) = Engagement (0-40) + Audience Size (0-30) + Relevance (0-30)

Engagement rate = (avg_likes + avg_comments) / avg_views * 100
Engagement score = min(40, 40 * log(1 + rate) / log(11))
  1% ER  → ~16 pts  |  3% ER → ~28 pts  |  10% ER → 40 pts

Audience size tiers:
  <1k → 0 | 1k-10k → 5 | 10k-50k → 15 | 50k-200k → 22 | 200k-500k → 26 | 500k+ → 30
```

---

## Requirements (`requirements.txt`)

```
langgraph>=0.2.0
langchain-anthropic>=0.3.0
langchain-core>=0.3.0
google-api-python-client>=2.140.0
pydantic>=2.9.0
python-dotenv>=1.0.0
typing-extensions>=4.12.0
```

SQLite is part of the Python stdlib — no extra package needed.

---

## Setup & Usage

```bash
# One-time setup
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in YOUTUBE_API_KEY + ANTHROPIC_API_KEY

# Default run
python main.py

# Custom run
python main.py \
  --keywords "marketing attribution" "google analytics" "SaaS marketing" \
  --min-subscribers 5000 \
  --min-engagement 1.0 \
  --max-results 20
```

### Mark a channel as sent (prevents re-emailing)
```bash
sqlite3 output/influencers.db \
  "UPDATE outreach_emails SET sent_at = datetime('now') WHERE channel_id = 'UC...';"
```

---

## Verification Checklist

1. Test YouTube client: `from src.tools.youtube_client import YouTubeClient; YouTubeClient().search_channels("marketing analytics", 3)`
2. Test DB layer: `from src.db.database import Database; db = Database(); db.init_db(); print(db.get_emailed_channel_ids())`
3. Inspect graph: `from src.graph import build_graph; print(build_graph().get_graph().draw_mermaid())`
4. Smoke test (low quota): `python main.py --max-results 3 --min-subscribers 1000 --min-engagement 0.1`
5. Dedup test: run twice — second run skips all channels already in `outreach_emails`
6. Query DB: `sqlite3 output/influencers.db "SELECT channel_title, composite_score FROM scored_influencers ORDER BY composite_score DESC LIMIT 10"`
