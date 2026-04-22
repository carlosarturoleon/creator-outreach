"""
Mark scored influencers as selected based on a minimum composite score threshold.

Usage:
    python scripts/select_influencers.py           # default threshold: 40
    python scripts/select_influencers.py --min-score 50
    python scripts/select_influencers.py --min-score 50 --reset  # clear previous selections first

After running, the outreach_emails pipeline will only email selected channels.
"""

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.db.database import Database

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "output", "influencers.db")


def main():
    parser = argparse.ArgumentParser(description="Select influencers by score threshold")
    parser.add_argument("--min-score", type=float, default=40.0,
                        help="Minimum composite score to select (default: 40)")
    parser.add_argument("--reset", action="store_true",
                        help="Clear all existing selections before applying new threshold")
    args = parser.parse_args()

    db = Database()
    db.migrate_scoring_v2()

    con = sqlite3.connect(DB_PATH)

    if args.reset:
        con.execute("UPDATE scored_influencers SET selected = 0, selected_at = NULL")
        con.commit()
        print("Cleared all existing selections.")

    # Show distribution before selecting
    rows = con.execute("""
        SELECT s.composite_score, c.channel_title, c.subscriber_count,
               ROUND(c.engagement_rate, 2) as er, s.selected
        FROM scored_influencers s
        JOIN channels c ON c.channel_id = s.channel_id
        WHERE c.subscriber_count >= 1000
        ORDER BY s.composite_score DESC
    """).fetchall()
    con.close()

    total = len(rows)
    qualifying = [r for r in rows if r[0] >= args.min_score]

    print(f"\nScore distribution (total scored: {total}):")
    for threshold in (70, 60, 50, 40, 30):
        count = sum(1 for r in rows if r[0] >= threshold)
        print(f"  >= {threshold}: {count} channels")

    print(f"\nSelecting {len(qualifying)} channels with score >= {args.min_score}:")
    print(f"\n{'Score':<7} {'Subs':<8} {'ER%':<6} {'Channel'}")
    print("-" * 60)
    for score, title, subs, er, _ in qualifying[:30]:
        print(f"{score:<7.1f} {subs:<8,} {er:<6} {title}")
    if len(qualifying) > 30:
        print(f"  ... and {len(qualifying) - 30} more")

    if not qualifying:
        print("No channels meet the threshold. Try lowering --min-score.")
        return

    con2 = sqlite3.connect(DB_PATH)
    ids = [r[0] for r in con2.execute(
        """SELECT s.channel_id FROM scored_influencers s
           JOIN channels c ON c.channel_id = s.channel_id
           WHERE s.composite_score >= ? AND c.subscriber_count >= 1000""",
        (args.min_score,)
    ).fetchall()]
    con2.close()

    updated = db.select_influencers(ids)
    print(f"\n{updated} channels marked as selected.")
    print("Run `python scripts/export_csv.py` to export, or start the pipeline for email generation.")


if __name__ == "__main__":
    main()
