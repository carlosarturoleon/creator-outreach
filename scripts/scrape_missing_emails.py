"""
Standalone script: scrape contact emails from websites linked in channel descriptions,
for channels that are missing a contact email but have been scored (i.e. would appear
in the export CSV).

Targets the same channels as export_csv.py's scored query:
  - Joined with scored_influencers (llm_score IS NOT NULL)
  - contact_email IS NULL
  - no_email = 0
  - Not already sent
  - Ordered by llm_score DESC, composite_score DESC

Usage (run from project root):
    python -m scripts.scrape_missing_emails
    python -m scripts.scrape_missing_emails --limit 50
    python -m scripts.scrape_missing_emails --all   # include unscored channels too
"""
import argparse
import json
import sqlite3

from src.db.database import Database
from src.tools.web_scraper import extract_urls_from_text, scrape_emails_from_url

DB_PATH = "output/influencers.db"

_SCORED_QUERY = """
    SELECT c.channel_id, c.channel_title, c.description
    FROM channels c
    JOIN scored_influencers s ON c.channel_id = s.channel_id
    WHERE c.contact_email IS NULL
      AND (c.no_email IS NULL OR c.no_email = 0)
      AND s.llm_score IS NOT NULL
      AND c.channel_id NOT IN (SELECT channel_id FROM outreach_emails WHERE sent_at IS NOT NULL)
    ORDER BY s.llm_score DESC, s.composite_score DESC
"""

_ALL_QUERY = """
    SELECT channel_id, channel_title, description
    FROM channels
    WHERE contact_email IS NULL
      AND (no_email IS NULL OR no_email = 0)
    ORDER BY subscriber_count DESC
"""


def main():
    parser = argparse.ArgumentParser(description="Scrape contact emails for channels missing one")
    parser.add_argument("--limit", type=int, default=None, help="Max channels to process")
    parser.add_argument("--all", action="store_true", help="Include unscored channels (default: scored only)")
    args = parser.parse_args()

    db = Database(DB_PATH)
    db.init_db()
    db.migrate_add_contact_email()
    db.migrate_add_contact_emails()
    db.migrate_add_no_email()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    query = _ALL_QUERY if args.all else _SCORED_QUERY
    rows = conn.execute(query).fetchall()
    conn.close()

    if args.limit:
        rows = rows[:args.limit]

    total = len(rows)
    if total == 0:
        print("No channels missing a contact email — nothing to do.")
        return

    mode = "all channels" if args.all else "scored channels (llm_score IS NOT NULL)"
    print(f"Found {total} channels without a contact email [{mode}]")
    print()

    found_count = 0
    for i, row in enumerate(rows, 1):
        cid = row["channel_id"]
        title = row["channel_title"] or cid
        description = row["description"] or ""

        urls = extract_urls_from_text(description)
        if not urls:
            print(f"  [{i}/{total}] {title} — no URLs in description, skipping")
            continue

        all_emails: list[str] = []
        for url in urls[:3]:
            found = scrape_emails_from_url(url)
            for email in found:
                if email not in all_emails:
                    all_emails.append(email)

        if all_emails:
            primary = all_emails[0]
            print(f"  [{i}/{total}] {title} — found {len(all_emails)} email(s): {', '.join(all_emails)}")
            ch = {"channel_id": cid, "contact_email": primary, "contact_emails": all_emails}
            # Minimal upsert — only update email fields, preserve everything else
            conn2 = sqlite3.connect(DB_PATH)
            conn2.execute(
                "UPDATE channels SET contact_email = ?, contact_emails = ? WHERE channel_id = ?",
                (primary, json.dumps(all_emails), cid),
            )
            conn2.commit()
            conn2.close()
            found_count += 1
        else:
            print(f"  [{i}/{total}] {title} — scraped {len(urls)} URL(s), no email found")

    print()
    print(f"Done — {found_count}/{total} channels now have a contact email.")


if __name__ == "__main__":
    main()
