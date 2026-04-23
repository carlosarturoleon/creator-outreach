"""
view_influencers.py — Show top-scored influencers with email status.

Usage:
    python view_influencers.py                # all scored influencers
    python view_influencers.py --missing-only # only those without a contact email
"""
import argparse

from src.db.database import Database


def main() -> None:
    parser = argparse.ArgumentParser(description="View top scored influencers")
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="Show only influencers missing a contact email",
    )
    args = parser.parse_args()

    db = Database()
    with db._connect() as conn:
        rows = conn.execute("""
            SELECT
                s.channel_id,
                c.channel_title,
                c.subscriber_count,
                s.llm_score,
                s.composite_score,
                COALESCE(e.contact_email, c.contact_email) AS contact_email,
                e.sent_at
            FROM scored_influencers s
            JOIN channels c ON s.channel_id = c.channel_id
            LEFT JOIN outreach_emails e ON s.channel_id = e.channel_id
            WHERE s.llm_score IS NOT NULL
            ORDER BY s.llm_score DESC, s.composite_score DESC
        """).fetchall()

    if not rows:
        print("No LLM-scored influencers found. Run the pipeline first.")
        return

    if args.missing_only:
        rows = [r for r in rows if not r["contact_email"]]
        if not rows:
            print("All scored influencers already have a contact email.")
            return

    # Column widths
    W_TITLE = 40
    W_SUBS = 8
    W_LLM = 4
    W_COMP = 9
    W_EMAIL = 32
    W_SENT = 6

    header = (
        f"{'#':<4} "
        f"{'Channel':<{W_TITLE}} "
        f"{'Subs':>{W_SUBS}} "
        f"{'LLM':>{W_LLM}} "
        f"{'Composite':>{W_COMP}} "
        f"{'Email':<{W_EMAIL}} "
        f"{'Sent':<{W_SENT}}"
    )
    print(header)
    print("-" * len(header))

    for i, row in enumerate(rows, 1):
        title = row["channel_title"] or ""
        if len(title) > W_TITLE:
            title = title[:W_TITLE - 1] + "…"

        subs = row["subscriber_count"] or 0
        llm = row["llm_score"] if row["llm_score"] is not None else "-"
        comp = f"{row['composite_score']:.1f}" if row["composite_score"] else "-"

        email = row["contact_email"] or "MISSING"
        if len(email) > W_EMAIL:
            email = email[:W_EMAIL - 1] + "…"

        sent = "yes" if row["sent_at"] else "no"

        print(
            f"{i:<4} "
            f"{title:<{W_TITLE}} "
            f"{subs:>{W_SUBS},} "
            f"{str(llm):>{W_LLM}} "
            f"{comp:>{W_COMP}} "
            f"{email:<{W_EMAIL}} "
            f"{sent:<{W_SENT}}"
        )

    missing = sum(1 for r in rows if not r["contact_email"])
    print(f"\n{len(rows)} influencer(s) shown — {missing} missing email.")
    if missing:
        print("To set an email:  python set_email.py \"Channel Name\" email@example.com")
        print("To bulk-import:   python import_emails.py output/channels_export.csv")


if __name__ == "__main__":
    main()
