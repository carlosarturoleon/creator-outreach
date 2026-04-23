"""
filter_and_score_from_db.py — Apply Windsor.ai niche filter to all enriched
channels in the DB, then run deterministic scoring on those that pass.

Useful after a large pipeline run where many channels were enriched but not
all made it through the filter due to pipeline state issues.

Usage:
    python scripts/filter_and_score_from_db.py
    python scripts/filter_and_score_from_db.py --min-subscribers 5000
    python scripts/filter_and_score_from_db.py --min-subscribers 1000 --min-engagement 0.5
"""
import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.db.database import Database
from src.nodes.filter_influencers import WINDSOR_AI_NICHES
from src.nodes.score_influencers import (
    _audience_size_score,
    _engagement_score,
    _tutorial_score,
    _upload_recency_score,
)
from src.scoring.keyword_scorer import score_channel_relevance

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "output", "influencers.db")


def load_enriched_channels(min_subscribers: int, min_engagement: float) -> list[dict]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT
            channel_id, channel_title, description, subscriber_count,
            engagement_rate, upload_frequency_days, avg_views_per_video,
            avg_likes_per_video, avg_comments_per_video,
            keywords, recent_video_titles, country, default_language
        FROM channels
        WHERE subscriber_count >= ?
          AND engagement_rate >= ?
        ORDER BY subscriber_count DESC
    """, (min_subscribers, min_engagement)).fetchall()
    con.close()

    channels = []
    for row in rows:
        d = dict(row)
        for field in ("keywords", "recent_video_titles"):
            raw = d.get(field)
            try:
                d[field] = json.loads(raw) if raw else []
            except (json.JSONDecodeError, TypeError):
                d[field] = []
        channels.append(d)
    return channels


def apply_niche_filter(channels: list[dict]) -> list[dict]:
    passed = []
    for ch in channels:
        text_blob = " ".join([
            ch.get("description", ""),
            " ".join(ch.get("keywords", [])),
            " ".join(ch.get("recent_video_titles", [])),
            ch.get("channel_title", ""),
        ]).lower()
        if any(niche in text_blob for niche in WINDSOR_AI_NICHES):
            passed.append(ch)
    return passed


def main() -> None:
    parser = argparse.ArgumentParser(description="Filter + score all DB channels")
    parser.add_argument("--min-subscribers", type=int, default=1000)
    parser.add_argument("--min-engagement", type=float, default=0.0)
    args = parser.parse_args()

    db = Database()
    db.migrate_scoring_v2()

    print(f"Loading enriched channels (min subs: {args.min_subscribers:,}, min engagement: {args.min_engagement})...")
    channels = load_enriched_channels(args.min_subscribers, args.min_engagement)
    print(f"  {len(channels)} channels meet subscriber/engagement floor")

    filtered = apply_niche_filter(channels)
    print(f"  {len(filtered)} passed Windsor.ai niche filter ({len(channels) - len(filtered)} dropped)\n")

    if not filtered:
        print("No channels passed the filter. Try broadening --min-subscribers or --min-engagement.")
        sys.exit(0)

    print("Sample of channels that will be scored:")
    for ch in filtered[:10]:
        print(f"  {ch['channel_title']} ({ch['subscriber_count']:,} subs, {ch['engagement_rate']:.2f}% eng)")
    if len(filtered) > 10:
        print(f"  ... and {len(filtered) - 10} more")

    print(f"\nProceed with scoring {len(filtered)} channels? [y/N] ", end="", flush=True)
    answer = input().strip().lower()
    if answer != "y":
        print("Aborted.")
        sys.exit(0)

    # Mark as filtered in DB
    db.mark_channels_filtered([ch["channel_id"] for ch in filtered])

    # Score
    scored = []
    for ch in filtered:
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

    print(f"{'Rank':<5} {'Score':<7} {'Subs':>8} {'Channel'}")
    print("-" * 60)
    for i, ch in enumerate(scored[:20], 1):
        print(f"{i:<5} {ch['composite_score']:<7.1f} {ch['subscriber_count']:>8,}  {ch['channel_title']}")

    print(f"\n{len(scored)} channel(s) scored and saved to DB.")
    print("Next: run `python regenerate_emails.py` to generate emails for LLM-scored channels,")
    print("      or run the full LLM scoring batch first via the pipeline.")


if __name__ == "__main__":
    main()
