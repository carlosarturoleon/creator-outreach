"""
Score all filtered channels already in the DB without running the full pipeline.

Reads from the channels table (passed_filter_at IS NOT NULL), applies the
5-component affiliate-fit scoring, and writes results to scored_influencers.

Usage:
    python scripts/score_from_db.py
"""

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.db.database import Database
from src.nodes.score_influencers import (
    _engagement_score,
    _audience_size_score,
    _tutorial_score,
    _upload_recency_score,
)
from src.scoring.keyword_scorer import score_channel_relevance

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "output", "influencers.db")


def load_filtered_channels() -> list[dict]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT
            channel_id, channel_title, description, subscriber_count,
            engagement_rate, upload_frequency_days, avg_views_per_video,
            avg_likes_per_video, avg_comments_per_video,
            keywords, recent_video_titles, country, default_language
        FROM channels
        WHERE engagement_rate IS NOT NULL
          AND subscriber_count > 0
        ORDER BY subscriber_count DESC
    """).fetchall()
    con.close()

    channels = []
    for row in rows:
        d = dict(row)
        # Parse JSON fields back to lists
        for field in ("keywords", "recent_video_titles"):
            raw = d.get(field)
            if raw:
                try:
                    d[field] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    d[field] = []
            else:
                d[field] = []
        channels.append(d)
    return channels


def main():
    db = Database()
    db.migrate_scoring_v2()

    channels = load_filtered_channels()
    print(f"Scoring {len(channels)} channels...")

    scored = []
    for ch in channels:
        engagement_pts = _engagement_score(ch.get("engagement_rate", 0.0))
        size_pts = _audience_size_score(ch.get("subscriber_count", 0))
        tutorial_pts = _tutorial_score(ch.get("recent_video_titles", []))
        upload_pts = _upload_recency_score(ch.get("upload_frequency_days", 0.0))

        kw_result = score_channel_relevance(ch)
        relevance_pts = round(kw_result["relevance_score"] * (25.0 / 30.0), 2)

        composite = round(engagement_pts + size_pts + relevance_pts + tutorial_pts + upload_pts, 2)

        scored_ch = {
            "channel_id": ch["channel_id"],
            "channel_title": ch["channel_title"],
            "engagement_rate": ch.get("engagement_rate", 0.0),
            "subscriber_count": ch.get("subscriber_count", 0),
            "composite_score": composite,
            "score_breakdown": {
                "engagement": engagement_pts,
                "audience_size": size_pts,
                "relevance": relevance_pts,
                "tutorial": tutorial_pts,
                "upload_recency": upload_pts,
            },
            "relevance_rationale": kw_result["relevance_rationale"],
            "niche_tags": kw_result["niche_tags"],
        }
        db.upsert_scored_influencer(scored_ch)
        scored.append(scored_ch)

    scored.sort(key=lambda x: x["composite_score"], reverse=True)

    print(f"\nTop 10 by composite score:")
    print(f"{'Rank':<5} {'Score':<7} {'Eng':<6} {'Size':<6} {'Rel':<6} {'Tut':<6} {'Up':<5} {'Channel'}")
    print("-" * 80)
    for i, ch in enumerate(scored[:10], 1):
        bd = ch["score_breakdown"]
        print(
            f"{i:<5} {ch['composite_score']:<7.1f} "
            f"{bd['engagement']:<6.1f} {bd['audience_size']:<6.1f} "
            f"{bd['relevance']:<6.1f} {bd['tutorial']:<6.1f} {bd['upload_recency']:<5.1f} "
            f"{ch['channel_title']}"
        )

    print(f"\nScored {len(scored)} channels → saved to scored_influencers table.")
    print("Run `python scripts/export_csv.py` to get the full ranked CSV.")


if __name__ == "__main__":
    main()
