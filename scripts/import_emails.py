"""
import_emails.py — Bulk-import contact emails from a filled-in CSV.

Reads the `channel_id` and `contact_email` columns from the export CSV,
updates both `channels` and `outreach_emails` tables for any row that has
a non-empty email. Multiple emails can be entered as comma-separated values
in the `contact_email` cell (e.g. "a@x.com, b@x.com"); the first becomes
the primary and all are saved to `contact_emails`.

Usage:
    python import_emails.py output/channels_export.csv
"""
import csv
import json
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
            sample = f.read(4096)
            f.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=",;")
            reader = csv.DictReader(f, dialect=dialect)
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
    db.migrate_add_no_email()

    updated = 0
    marked_no_email = 0
    skipped_empty = 0
    not_found = 0

    no_email_ids = []

    with db._connect() as conn:
        for row in rows:
            channel_id = row.get("channel_id", "").strip()
            email_raw = row.get("contact_email", "").strip()
            no_email = str(row.get("no_email", "")).strip()

            exists = conn.execute(
                "SELECT channel_title FROM channels WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()

            if not exists:
                not_found += 1
                continue

            if no_email == "1":
                no_email_ids.append(channel_id)
                continue

            if not email_raw:
                skipped_empty += 1
                continue

            # Support comma-separated multiple emails; first is primary
            emails = [e.strip() for e in email_raw.split(",") if e.strip()]
            primary_email = emails[0]

            conn.execute(
                "UPDATE channels SET contact_email = ?, contact_emails = ? WHERE channel_id = ?",
                (primary_email, json.dumps(emails), channel_id),
            )
            conn.execute(
                "UPDATE outreach_emails SET contact_email = ? WHERE channel_id = ?",
                (primary_email, channel_id),
            )
            label = email_raw if len(emails) == 1 else f"{primary_email} (+{len(emails)-1} more)"
            print(f"  ✓ {exists['channel_title']}: {label}")
            updated += 1

    if no_email_ids:
        marked_no_email = db.mark_no_email(no_email_ids)
        print(f"  — {marked_no_email} channel(s) flagged as no-email (will be hidden from future exports)")

    print(f"\nDone — {updated} updated, {marked_no_email} no-email, {skipped_empty} skipped (empty), {not_found} not found in DB.")
    if updated:
        print("Run: python send_emails.py --dry-run   to preview")
        print("Run: python send_emails.py             to send")


if __name__ == "__main__":
    main()
