# Windsor.ai YouTube Influencer Finder

LangGraph agent pipeline that finds, scores, and generates outreach emails for YouTube influencers relevant to the Windsor.ai affiliate program.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # add YOUTUBE_API_KEY and ANTHROPIC_API_KEY
```

## Run

```bash
python main.py

# Custom run
python main.py \
  --keywords "marketing attribution" "google analytics" "SaaS marketing" \
  --min-subscribers 5000 \
  --min-engagement 1.0 \
  --max-results 20
```

## Mark a channel as sent (prevents re-emailing on future runs)

```bash
sqlite3 output/influencers.db \
  "UPDATE outreach_emails SET sent_at = datetime('now') WHERE channel_id = 'UC...';"
```

## Query top scored influencers

```bash
sqlite3 output/influencers.db \
  "SELECT channel_title, composite_score FROM scored_influencers ORDER BY composite_score DESC LIMIT 10;"
```

## Architecture

See PLAN.md for full architecture documentation.

Graph flow:
```
search_channels → deduplicate_vs_db → enrich_channel_data → filter_influencers
                                                                    ↓
                                                          score_influencers → generate_emails → save_results
```

## Key files

- src/state.py — GraphState TypedDict
- src/graph.py — LangGraph StateGraph assembly
- src/db/database.py — SQLite persistence layer
- src/tools/youtube_client.py — YouTube Data API v3 wrapper
- src/nodes/ — one file per graph node
