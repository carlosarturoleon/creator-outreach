"""
Export all enriched channels from SQLite to a CSV file for analysis.
If scoring has been run, score columns are included (sorted by composite_score DESC).
Otherwise sorted by subscriber_count DESC.

Usage:
    python scripts/export_csv.py

Output: output/channels_export.csv
"""

import csv
import json
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "output", "influencers.db")
CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "output", "channels_export.csv")


def parse_json_list(value: str, limit: int = None) -> str:
    """Parse a JSON array stored as text and return pipe-separated string."""
    if not value:
        return ""
    try:
        items = json.loads(value)
        if not isinstance(items, list):
            return str(value)
        if limit:
            items = items[:limit]
        return " | ".join(str(i) for i in items)
    except (json.JSONDecodeError, TypeError):
        return str(value)


def has_scores(con: sqlite3.Connection) -> bool:
    row = con.execute("SELECT COUNT(*) FROM scored_influencers").fetchone()
    return row[0] > 0


def main():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    # Ensure no_email column exists (idempotent migration)
    cols = [r[1] for r in con.execute("PRAGMA table_info(channels)").fetchall()]
    if "no_email" not in cols:
        con.execute("ALTER TABLE channels ADD COLUMN no_email INTEGER DEFAULT 0")
        con.commit()

    scored = has_scores(con)

    if scored:
        query = """
            SELECT
                c.channel_title,
                c.channel_id,
                c.subscriber_count,
                ROUND(c.engagement_rate, 4)        AS engagement_rate,
                ROUND(c.upload_frequency_days, 1)  AS upload_frequency_days,
                ROUND(c.avg_views_per_video, 0)    AS avg_views_per_video,
                ROUND(c.avg_likes_per_video, 1)    AS avg_likes_per_video,
                ROUND(c.avg_comments_per_video, 1) AS avg_comments_per_video,
                c.total_view_count,
                c.video_count,
                c.country,
                c.default_language,
                c.search_keyword,
                c.keywords,
                c.recent_video_titles,
                c.description,
                'https://youtube.com/channel/' || c.channel_id AS youtube_url,
                c.contact_email,
                COALESCE(c.no_email, 0) AS no_email,
                CASE WHEN c.passed_filter_at IS NOT NULL THEN 1 ELSE 0 END AS passed_filter,
                c.first_seen_at,
                ROUND(s.composite_score, 2)        AS composite_score,
                ROUND(s.engagement_score, 2)       AS engagement_score,
                ROUND(s.audience_size_score, 2)    AS audience_size_score,
                ROUND(s.relevance_score, 2)        AS relevance_score,
                ROUND(s.tutorial_score, 2)         AS tutorial_score,
                ROUND(s.upload_recency_score, 2)   AS upload_recency_score,
                s.selected,
                s.llm_score,
                s.llm_rationale,
                s.relevance_rationale,
                s.niche_tags
            FROM channels c
            JOIN scored_influencers s ON c.channel_id = s.channel_id
            WHERE c.subscriber_count >= 1000
              AND s.llm_score IS NOT NULL
              AND c.channel_id NOT IN (SELECT channel_id FROM outreach_emails WHERE sent_at IS NOT NULL)
              AND (c.no_email IS NULL OR c.no_email = 0)
            ORDER BY s.llm_score DESC, s.composite_score DESC
        """
        fieldnames = [
            "channel_title", "channel_id", "subscriber_count", "engagement_rate",
            "upload_frequency_days", "avg_views_per_video", "avg_likes_per_video",
            "avg_comments_per_video", "total_view_count", "video_count",
            "country", "default_language", "search_keyword",
            "keywords", "recent_video_titles", "description",
            "youtube_url", "contact_email", "no_email", "passed_filter", "first_seen_at",
            "composite_score", "engagement_score", "audience_size_score",
            "relevance_score", "tutorial_score", "upload_recency_score",
            "selected", "llm_score", "llm_rationale", "relevance_rationale", "niche_tags",
        ]
    else:
        query = """
            SELECT
                channel_title,
                channel_id,
                subscriber_count,
                ROUND(engagement_rate, 4)        AS engagement_rate,
                ROUND(upload_frequency_days, 1)  AS upload_frequency_days,
                ROUND(avg_views_per_video, 0)    AS avg_views_per_video,
                ROUND(avg_likes_per_video, 1)    AS avg_likes_per_video,
                ROUND(avg_comments_per_video, 1) AS avg_comments_per_video,
                total_view_count,
                video_count,
                country,
                default_language,
                search_keyword,
                keywords,
                recent_video_titles,
                description,
                contact_email,
                COALESCE(no_email, 0) AS no_email,
                CASE WHEN passed_filter_at IS NOT NULL THEN 1 ELSE 0 END AS passed_filter,
                first_seen_at
            FROM channels
            WHERE (no_email IS NULL OR no_email = 0)
            ORDER BY subscriber_count DESC
        """
        fieldnames = [
            "channel_title", "channel_id", "subscriber_count", "engagement_rate",
            "upload_frequency_days", "avg_views_per_video", "avg_likes_per_video",
            "avg_comments_per_video", "total_view_count", "video_count",
            "country", "default_language", "search_keyword",
            "keywords", "recent_video_titles", "description",
            "contact_email", "no_email", "passed_filter", "first_seen_at",
        ]

    rows = con.execute(query).fetchall()
    con.close()

    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            d = dict(row)
            d["keywords"] = parse_json_list(d.get("keywords"))
            d["recent_video_titles"] = parse_json_list(d.get("recent_video_titles"), limit=5)
            if "niche_tags" in d:
                d["niche_tags"] = parse_json_list(d.get("niche_tags"))
            if d.get("description"):
                d["description"] = d["description"][:300]
            writer.writerow(d)

    mode = "scored (sorted by llm_score, composite_score)" if scored else "unscored (sorted by subscriber_count)"
    print(f"Exported {len(rows)} channels [{mode}] → {os.path.abspath(CSV_PATH)}")


if __name__ == "__main__":
    main()
