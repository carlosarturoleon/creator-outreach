"""
extract_emails_from_descriptions.py — One-off scan of all channel descriptions
to extract contact emails and save them to the DB for channels missing one.

Usage:
    python scripts/extract_emails_from_descriptions.py
"""
import re
import sqlite3

DB_PATH = "output/influencers.db"
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT channel_id, channel_title, description FROM channels WHERE contact_email IS NULL OR contact_email = ''"
    ).fetchall()

    print(f"Scanning {len(rows)} channels without a contact email...\n")

    updated = 0
    for row in rows:
        description = row["description"] or ""
        m = EMAIL_RE.search(description)
        if m:
            email = m.group(0)
            conn.execute(
                "UPDATE channels SET contact_email = ? WHERE channel_id = ?",
                (email, row["channel_id"]),
            )
            print(f"  ✓ {row['channel_title']}: {email}")
            updated += 1

    conn.commit()
    conn.close()
    print(f"\nDone — {updated} emails extracted out of {len(rows)} channels scanned.")


if __name__ == "__main__":
    main()
