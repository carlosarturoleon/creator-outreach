import json
import os
import sqlite3
from datetime import datetime, timezone


class Database:
    def __init__(self, db_path: str = "output/influencers.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS channels (
                    channel_id TEXT PRIMARY KEY,
                    channel_title TEXT,
                    description TEXT,
                    subscriber_count INTEGER,
                    total_view_count INTEGER,
                    video_count INTEGER,
                    country TEXT,
                    default_language TEXT,
                    keywords TEXT,
                    avg_views_per_video REAL,
                    avg_likes_per_video REAL,
                    avg_comments_per_video REAL,
                    engagement_rate REAL,
                    upload_frequency_days REAL,
                    recent_video_titles TEXT,
                    search_keyword TEXT,
                    first_seen_at TEXT,
                    last_updated_at TEXT,
                    passed_filter_at TEXT
                );

                CREATE TABLE IF NOT EXISTS scored_influencers (
                    channel_id TEXT PRIMARY KEY REFERENCES channels(channel_id),
                    composite_score REAL,
                    engagement_score REAL,
                    audience_size_score REAL,
                    relevance_score REAL,
                    relevance_rationale TEXT,
                    niche_tags TEXT,
                    scored_at TEXT
                );

                CREATE TABLE IF NOT EXISTS outreach_emails (
                    channel_id TEXT PRIMARY KEY REFERENCES channels(channel_id),
                    subject_line TEXT,
                    email_body TEXT,
                    personalization_hooks TEXT,
                    generated_at TEXT,
                    sent_at TEXT
                );
            """)

    def get_emailed_channel_ids(self) -> set[str]:
        """Return channel_ids where the outreach email has been marked as sent.
        Channels with a generated-but-unsent email (sent_at IS NULL) are NOT skipped,
        allowing email regeneration for pending outreach.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT channel_id FROM outreach_emails WHERE sent_at IS NOT NULL"
            ).fetchall()
        return {row["channel_id"] for row in rows}

    def upsert_channel(self, data: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT first_seen_at FROM channels WHERE channel_id = ?",
                (data["channel_id"],)
            ).fetchone()
            first_seen = existing["first_seen_at"] if existing else now

            conn.execute("""
                INSERT INTO channels (
                    channel_id, channel_title, description,
                    subscriber_count, total_view_count, video_count,
                    country, default_language, keywords,
                    avg_views_per_video, avg_likes_per_video, avg_comments_per_video,
                    engagement_rate, upload_frequency_days, recent_video_titles,
                    search_keyword, first_seen_at, last_updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    channel_title = excluded.channel_title,
                    description = excluded.description,
                    subscriber_count = excluded.subscriber_count,
                    total_view_count = excluded.total_view_count,
                    video_count = excluded.video_count,
                    country = excluded.country,
                    default_language = excluded.default_language,
                    keywords = excluded.keywords,
                    avg_views_per_video = excluded.avg_views_per_video,
                    avg_likes_per_video = excluded.avg_likes_per_video,
                    avg_comments_per_video = excluded.avg_comments_per_video,
                    engagement_rate = excluded.engagement_rate,
                    upload_frequency_days = excluded.upload_frequency_days,
                    recent_video_titles = excluded.recent_video_titles,
                    search_keyword = excluded.search_keyword,
                    last_updated_at = excluded.last_updated_at
            """, (
                data.get("channel_id"),
                data.get("channel_title"),
                data.get("description"),
                data.get("subscriber_count", 0),
                data.get("total_view_count", 0),
                data.get("video_count", 0),
                data.get("country"),
                data.get("default_language"),
                json.dumps(data.get("keywords", [])),
                data.get("avg_views_per_video", 0.0),
                data.get("avg_likes_per_video", 0.0),
                data.get("avg_comments_per_video", 0.0),
                data.get("engagement_rate", 0.0),
                data.get("upload_frequency_days", 0.0),
                json.dumps(data.get("recent_video_titles", [])),
                data.get("search_keyword"),
                first_seen,
                now,
            ))

    def upsert_scored_influencer(self, data: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        breakdown = data.get("score_breakdown", {})
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO scored_influencers (
                    channel_id, composite_score, engagement_score,
                    audience_size_score, relevance_score,
                    relevance_rationale, niche_tags, scored_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    composite_score = excluded.composite_score,
                    engagement_score = excluded.engagement_score,
                    audience_size_score = excluded.audience_size_score,
                    relevance_score = excluded.relevance_score,
                    relevance_rationale = excluded.relevance_rationale,
                    niche_tags = excluded.niche_tags,
                    scored_at = excluded.scored_at
            """, (
                data.get("channel_id"),
                data.get("composite_score", 0.0),
                breakdown.get("engagement", 0.0),
                breakdown.get("audience_size", 0.0),
                breakdown.get("relevance", 0.0),
                data.get("relevance_rationale", ""),
                json.dumps(data.get("niche_tags", [])),
                now,
            ))

    def mark_channels_filtered(self, channel_ids: list[str]) -> None:
        """Set passed_filter_at timestamp for channels that passed all filters."""
        if not channel_ids:
            return
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.executemany(
                "UPDATE channels SET passed_filter_at = ? WHERE channel_id = ?",
                [(now, cid) for cid in channel_ids],
            )

    def upsert_email(self, data: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO outreach_emails (
                    channel_id, subject_line, email_body,
                    personalization_hooks, generated_at, sent_at
                ) VALUES (?, ?, ?, ?, ?, NULL)
                ON CONFLICT(channel_id) DO UPDATE SET
                    subject_line = excluded.subject_line,
                    email_body = excluded.email_body,
                    personalization_hooks = excluded.personalization_hooks,
                    generated_at = excluded.generated_at
            """, (
                data.get("channel_id"),
                data.get("subject_line", ""),
                data.get("email_body", ""),
                json.dumps(data.get("personalization_hooks", [])),
                now,
            ))
