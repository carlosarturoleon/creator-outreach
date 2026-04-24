"""
import_emails.py — Bulk-import contact emails from a filled-in CSV.

Reads the `channel_id` and `contact_email` columns from the export CSV,
updates both `channels` and `outreach_emails` tables for any row that has
a non-empty email.

Usage:
    python import_emails.py output/channels_export.csv
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.database import Database


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python import_emails.py <csv_file>")
        sys.exit(1)

    csv_path = sys.argv[1]

    try:
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            if "channel_id" not in (reader.fieldnames or []):
                print("Error: CSV must have a 'channel_id' column.")
                sys.exit(1)
            if "contact_email" not in (reader.fieldnames or []):
                print("Error: CSV must have a 'contact_email' column.")
                sys.exit(1)
            rows = list(reader)
    except FileNotFoundError:
        print(f"File not found: {csv_path}")
        sys.exit(1)

    db = Database()
    updated = 0
    skipped_empty = 0
    not_found = 0

    with db._connect() as conn:
        for row in rows:
            channel_id = row.get("channel_id", "").strip()
            email = row.get("contact_email", "").strip()

            if not email:
                skipped_empty += 1
                continue

            exists = conn.execute(
                "SELECT channel_title FROM channels WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()

            if not exists:
                not_found += 1
                continue

            conn.execute(
                "UPDATE channels SET contact_email = ? WHERE channel_id = ?",
                (email, channel_id),
            )
            conn.execute(
                "UPDATE outreach_emails SET contact_email = ? WHERE channel_id = ?",
                (email, channel_id),
            )
            print(f"  ✓ {exists['channel_title']}: {email}")
            updated += 1

    print(f"\nDone — {updated} updated, {skipped_empty} skipped (empty), {not_found} not found in DB.")
    if updated:
        print("Run: python send_emails.py --dry-run   to preview")
        print("Run: python send_emails.py             to send")


if __name__ == "__main__":
    main()
