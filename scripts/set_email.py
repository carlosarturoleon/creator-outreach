"""
set_email.py — Set a contact email for a single influencer in the DB.

Usage:
    # By channel ID (exact)
    python set_email.py UCxxxxxx contact@channel.com

    # By partial channel name (case-insensitive)
    python set_email.py "Analytics with Ahmed" ahmed@example.com
"""
import sys

from src.db.database import Database


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python set_email.py <channel_id_or_name> <email>")
        sys.exit(1)

    identifier = sys.argv[1].strip()
    email = sys.argv[2].strip()

    db = Database()
    with db._connect() as conn:
        # Try exact channel_id first
        row = conn.execute(
            "SELECT channel_id, channel_title FROM channels WHERE channel_id = ?",
            (identifier,),
        ).fetchone()

        # Fall back to case-insensitive name search
        if not row:
            rows = conn.execute(
                "SELECT channel_id, channel_title FROM channels "
                "WHERE LOWER(channel_title) LIKE ?",
                (f"%{identifier.lower()}%",),
            ).fetchall()
            if len(rows) == 0:
                print(f"No channel found matching: {identifier!r}")
                sys.exit(1)
            if len(rows) > 1:
                print(f"Multiple channels match {identifier!r} — be more specific:")
                for r in rows:
                    print(f"  {r['channel_id']}  {r['channel_title']}")
                sys.exit(1)
            row = rows[0]

        channel_id = row["channel_id"]
        channel_title = row["channel_title"]

        conn.execute(
            "UPDATE channels SET contact_email = ? WHERE channel_id = ?",
            (email, channel_id),
        )
        conn.execute(
            "UPDATE outreach_emails SET contact_email = ? WHERE channel_id = ?",
            (email, channel_id),
        )

    print(f"Set email for '{channel_title}' ({channel_id}): {email}")


if __name__ == "__main__":
    main()
