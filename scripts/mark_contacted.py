"""
mark_contacted.py — Mark channels from an external contacts CSV as already contacted.

Reads a CSV with a 'link' column containing YouTube URLs (@handle, channel/UC..., /watch?v=),
matches them against the DB by channel_id or title, then:
  - Sets sent_at = now in outreach_emails (inserts a placeholder row if none exists)
  - Sets contact_email if the CSV row has one and the DB doesn't

Usage:
    python scripts/mark_contacted.py <csv_file>

Example:
    python scripts/mark_contacted.py "claude influencers - Sheet1.csv"
"""
import csv
import re
import sys
from datetime import datetime, timezone

DB_PATH = "output/influencers.db"


def extract_channel_id(link: str) -> str | None:
    """Extract UC... channel ID from a channel/ URL."""
    m = re.search(r"channel/(UC[\w-]+)", link)
    return m.group(1) if m else None


def extract_handle(link: str) -> str | None:
    """Extract @handle from a YouTube URL."""
    m = re.search(r"youtube\.com/@([\w.-]+)", link)
    return m.group(1).lower() if m else None


def is_youtube(link: str) -> bool:
    return "youtube.com" in link or "youtu.be" in link


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/mark_contacted.py <csv_file>")
        sys.exit(1)

    csv_path = sys.argv[1]

    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        print(f"File not found: {csv_path}")
        sys.exit(1)

    if "link" not in rows[0]:
        print("Error: CSV must have a 'link' column.")
        sys.exit(1)

    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc).isoformat()

    matched = 0
    skipped_non_yt = 0
    not_found = 0
    already_marked = 0

    for row in rows:
        link = (row.get("link") or "").strip()
        csv_email = (row.get("notes") or "").strip()
        # Extract a plain email from the notes field if present
        email_match = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", csv_email)
        email = email_match.group(0) if email_match else None

        if not is_youtube(link):
            skipped_non_yt += 1
            continue

        # Try to find channel in DB
        db_row = None

        # 1. Direct channel ID in URL
        channel_id = extract_channel_id(link)
        if channel_id:
            db_row = conn.execute(
                "SELECT channel_id, channel_title, contact_email FROM channels WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()

        # 2. @handle match against channel_title (fuzzy)
        if not db_row:
            handle = extract_handle(link)
            if handle:
                db_row = conn.execute(
                    "SELECT channel_id, channel_title, contact_email FROM channels "
                    "WHERE LOWER(REPLACE(channel_title, ' ', '')) LIKE ?",
                    (f"%{handle.replace('-', '').replace('_', '').replace('.', '')}%",),
                ).fetchone()

        if not db_row:
            print(f"  NOT FOUND: {link}")
            not_found += 1
            continue

        cid = db_row["channel_id"]
        title = db_row["channel_title"]

        # Check existing outreach_emails row
        existing = conn.execute(
            "SELECT sent_at, contact_email FROM outreach_emails WHERE channel_id = ?",
            (cid,),
        ).fetchone()

        if existing and existing["sent_at"]:
            already_marked += 1
            print(f"  SKIP (already marked): {title}")
            continue

        if existing:
            # Update sent_at
            conn.execute(
                "UPDATE outreach_emails SET sent_at = ? WHERE channel_id = ?",
                (now, cid),
            )
        else:
            # Insert placeholder row
            conn.execute(
                """INSERT INTO outreach_emails
                   (channel_id, subject_line, email_body, personalization_hooks, generated_at, sent_at, contact_email)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (cid, "[external outreach]", "[contacted outside pipeline]", "[]", now, now, email or db_row["contact_email"]),
            )

        # Update contact_email in channels if we have one and DB doesn't
        if email and not db_row["contact_email"]:
            conn.execute(
                "UPDATE channels SET contact_email = ? WHERE channel_id = ?",
                (email, cid),
            )

        print(f"  MARKED: {title} ({cid})")
        matched += 1

    conn.commit()
    conn.close()

    print(f"\nDone — {matched} marked as contacted, {already_marked} already marked, "
          f"{not_found} not found in DB, {skipped_non_yt} non-YouTube links skipped.")


if __name__ == "__main__":
    main()
