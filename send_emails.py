"""
send_emails.py — Send generated outreach emails via Gmail SMTP.

Reads unsent emails from the DB (sent_at IS NULL) and delivers them.
When EMAIL_TEST_OVERRIDE is set in .env, ALL emails go to that address
regardless of the actual contact_email — safe for testing.

Usage:
    # Dry run — preview what would be sent (no actual sending)
    python send_emails.py --dry-run

    # Send up to 3 emails (test mode — goes to EMAIL_TEST_OVERRIDE)
    python send_emails.py --limit 3

    # Send all pending emails to their real contact addresses
    python send_emails.py
"""
import argparse
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.config import settings
from src.db.database import Database


def _build_message(
    to_addr: str,
    subject: str,
    body: str,
    channel_title: str,
) -> MIMEMultipart:
    """Build a plain-text MIME email message."""
    msg = MIMEMultipart("alternative")
    msg["From"] = settings.email_from or settings.smtp_user
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["X-Windsor-Channel"] = channel_title  # debug header
    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg


def send_emails(limit: int = 0, dry_run: bool = False) -> None:
    db = Database()
    test_override = settings.email_test_override.strip()

    with db._connect() as conn:
        rows = conn.execute("""
            SELECT e.channel_id, e.subject_line, e.email_body, e.contact_email,
                   c.channel_title
            FROM outreach_emails e
            JOIN channels c ON e.channel_id = c.channel_id
            WHERE e.sent_at IS NULL
            ORDER BY e.generated_at DESC
        """).fetchall()

    if not rows:
        print("No unsent emails found in DB.")
        return

    candidates = list(rows)
    if limit:
        candidates = candidates[:limit]

    print(f"\n{'DRY RUN — ' if dry_run else ''}Sending {len(candidates)} email(s)")
    if test_override:
        print(f"TEST OVERRIDE: all emails redirected to {test_override}\n")
    else:
        print("WARNING: No EMAIL_TEST_OVERRIDE set — emails will go to real contacts!\n")

    if not dry_run:
        if not settings.smtp_user or not settings.smtp_password:
            print("Error: SMTP_USER and SMTP_PASSWORD must be set in .env")
            sys.exit(1)
        try:
            smtp = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
            smtp.ehlo()
            smtp.starttls()
            smtp.login(settings.smtp_user, settings.smtp_password)
            print(f"Connected to {settings.smtp_host}:{settings.smtp_port} as {settings.smtp_user}\n")
        except Exception as e:
            print(f"SMTP connection failed: {e}")
            sys.exit(1)

    sent_ids = []
    for i, row in enumerate(candidates, 1):
        channel_id = row["channel_id"]
        channel_title = row["channel_title"]
        subject = row["subject_line"]
        body = row["email_body"]
        contact_email = row["contact_email"]

        to_addr = test_override if test_override else contact_email

        if not to_addr:
            print(f"  [{i}/{len(candidates)}] SKIP {channel_title} — no email address")
            continue

        print(f"  [{i}/{len(candidates)}] {channel_title}")
        print(f"       To:      {to_addr}")
        print(f"       Subject: {subject}")
        print(f"       Body preview: {body[:120].strip()}...")
        print()

        if not dry_run:
            try:
                msg = _build_message(to_addr, subject, body, channel_title)
                smtp.sendmail(settings.smtp_user, to_addr, msg.as_string())
                sent_ids.append(channel_id)
                print(f"       ✓ Sent")
            except Exception as e:
                print(f"       ✗ Failed: {e}")
        else:
            sent_ids.append(channel_id)  # count as "would send" for dry run reporting

    if not dry_run:
        smtp.quit()
        if sent_ids:
            db.mark_emails_sent(sent_ids)
            print(f"\n{len(sent_ids)} email(s) sent and marked in DB.")
    else:
        print(f"\nDRY RUN complete — {len(sent_ids)} email(s) would be sent.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Send Windsor.ai affiliate outreach emails")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max number of emails to send (0 = all pending)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Preview emails without sending",
    )
    args = parser.parse_args()
    send_emails(limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
