# Windsor.ai YouTube Influencer Finder

LangGraph pipeline that finds, scores, and sends outreach emails to YouTube influencers relevant to the Windsor.ai affiliate program.

---

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # add YOUTUBE_API_KEY and ANTHROPIC_API_KEY
```

---

## Workflow

There are two ways to use this project:

### A. Full pipeline (find new channels from scratch)

Runs everything end-to-end: search YouTube → enrich → filter → score → generate emails.

```bash
python main.py --keywords "marketing attribution" "google analytics" --min-subscribers 5000
```

### B. Manual workflow (the one you actually use)

Run each step independently, review and add emails in between.

```
1. Discover & score  →  2. Export CSV  →  3. Add emails  →  4. Import emails  →  5. Generate text  →  6. Send
```

---

## Step-by-step commands

### 1. Discover and score channels

Find channels on YouTube and score them:

```bash
python main.py --keywords "looker studio" "google analytics" --stop-after-filter
```

Then run LLM scoring (rates affiliate fit 1–10 via Claude):

```bash
python scripts/llm_score_from_db.py
```

### 2. Export to CSV for review

```bash
python scripts/export_csv.py
```

Opens `output/channels_export.csv`. Review it — these are all scored channels not yet emailed.

### 3. Add contact emails to the CSV

Fill in the `contact_email` column for the channels you want to email. You can:
- Use emails extracted automatically (already in the column from `extract_emails_from_descriptions.py`)
- Add emails manually from channel About pages or LinkedIn

For channels where you can't find an email and want to permanently hide from future exports, set `no_email = 1` in that column. They'll be filtered out on the next export and won't appear again.

### 4. Import emails back to the DB

```bash
python scripts/import_emails.py output/channels_export.csv
```

Having a contact email is your approval signal — only channels with an email will be emailed. Channels marked `no_email = 1` are flagged in the DB and excluded from all future exports.

### 5. Generate email text

Calls Claude via Anthropic Batch API to write personalized outreach emails:

```bash
python scripts/generate_emails.py
```

Skips channels already sent. Results saved to DB.

### 6. Preview and send

```bash
# Preview — no emails sent
python send_emails.py --dry-run

# Send a test batch (goes to EMAIL_TEST_OVERRIDE address)
python send_emails.py --limit 3

# Send all pending emails
python send_emails.py
```

---

## Utility commands

### Extract emails from channel descriptions automatically

```bash
python scripts/extract_emails_from_descriptions.py
```

Scans channel descriptions in the DB for email addresses and saves them. Run before exporting CSV to pre-fill as many emails as possible.

### Check what's pending

```bash
# Channels with email not yet sent
sqlite3 output/influencers.db "
  SELECT c.channel_title, c.contact_email
  FROM scored_influencers si
  JOIN channels c USING (channel_id)
  WHERE c.contact_email IS NOT NULL AND length(c.contact_email) > 0
    AND si.channel_id NOT IN (SELECT channel_id FROM outreach_emails WHERE sent_at IS NOT NULL);"
```

### Check what's been sent

```bash
sqlite3 output/influencers.db "
  SELECT c.channel_title, oe.contact_email, oe.sent_at
  FROM outreach_emails oe
  JOIN channels c USING (channel_id)
  WHERE oe.sent_at IS NOT NULL
  ORDER BY oe.sent_at DESC;"
```

### Manage the affiliate promoter exclusion list

Channels whose contact email matches a known Windsor.ai affiliate are automatically skipped during email generation. Update this list weekly (or whenever the affiliate roster changes):

```bash
# Import / re-import from the latest promoters.csv (safe to run repeatedly — duplicates are ignored)
python scripts/import_promoters.py promoters.csv
```

Check how many promoters are currently in the DB:

```bash
sqlite3 output/influencers.db "SELECT COUNT(*) FROM affiliate_promoters;"
```

### Mark a channel as contacted (sent outside the pipeline)

```bash
python scripts/mark_contacted.py "contacts.csv"
```

CSV must have a `link` column with YouTube URLs.

### Mark a single channel as sent manually

```bash
sqlite3 output/influencers.db \
  "UPDATE outreach_emails SET sent_at = datetime('now') WHERE channel_id = 'UCxxx';"
```

### View top scored influencers

```bash
sqlite3 output/influencers.db \
  "SELECT channel_title, composite_score, llm_score FROM scored_influencers
   JOIN channels USING (channel_id)
   ORDER BY llm_score DESC, composite_score DESC LIMIT 20;"
```

---

## How approval works

There is no manual "select" step needed. The flow is:

| Signal | Meaning |
|---|---|
| Channel in `scored_influencers` | Passed filter, scored |
| `contact_email` set in `channels` | You reviewed it and want to email it |
| `outreach_emails.generated_at` set | Email text was generated |
| `outreach_emails.sent_at` set | Email was sent — will never be emailed again |

The `selected` flag in the DB is a legacy score-threshold marker and is not used in the current workflow.

---

## Architecture

```
search_channels → deduplicate_vs_db → enrich_channel_data → filter_influencers
                                                                    ↓
                                                    score_influencers → generate_emails → save_results
```

Key files:
- `src/state.py` — GraphState TypedDict
- `src/graph.py` — LangGraph StateGraph assembly
- `src/db/database.py` — SQLite persistence layer
- `src/tools/youtube_client.py` — YouTube Data API v3 wrapper
- `src/nodes/` — one file per graph node
- `scripts/` — standalone scripts for each manual step
