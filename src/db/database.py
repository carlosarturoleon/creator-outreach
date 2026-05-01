import json
import os
import sqlite3
from datetime import datetime


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
                    passed_filter_at TEXT,
                    contact_email TEXT,
                    contact_emails TEXT
                );

                CREATE TABLE IF NOT EXISTS scored_influencers (
                    channel_id TEXT PRIMARY KEY REFERENCES channels(channel_id),
                    composite_score REAL,
                    engagement_score REAL,
                    audience_size_score REAL,
                    relevance_score REAL,
                    tutorial_score REAL DEFAULT 0,
                    upload_recency_score REAL DEFAULT 0,
                    relevance_rationale TEXT,
                    niche_tags TEXT,
                    scored_at TEXT,
                    selected INTEGER DEFAULT 0,
                    selected_at TEXT
                );

                CREATE TABLE IF NOT EXISTS outreach_emails (
                    channel_id TEXT PRIMARY KEY REFERENCES channels(channel_id),
                    subject_line TEXT,
                    email_body TEXT,
                    personalization_hooks TEXT,
                    contact_email TEXT,
                    generated_at TEXT,
                    sent_at TEXT
                );

                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    keywords TEXT,
                    min_subscribers INTEGER,
                    min_engagement_rate REAL,
                    max_results_per_keyword INTEGER,
                    stop_after_filter INTEGER,
                    total_found INTEGER,
                    total_deduped INTEGER,
                    total_pre_filtered INTEGER,
                    total_enriched INTEGER,
                    total_filtered INTEGER,
                    total_scored INTEGER,
                    total_emailed INTEGER,
                    error_count INTEGER,
                    status TEXT
                );

                CREATE TABLE IF NOT EXISTS searched_keywords (
                    keyword TEXT PRIMARY KEY,
                    searched_at TEXT NOT NULL,
                    channels_found INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS run_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    logged_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    logger TEXT NOT NULL,
                    message TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS affiliate_promoters (
                    email TEXT PRIMARY KEY,
                    imported_at TEXT DEFAULT (datetime('now'))
                );
            """)

    def migrate_scoring_v2(self) -> None:
        """Add tutorial_score, upload_recency_score, selected, selected_at columns if missing."""
        with self._connect() as conn:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(scored_influencers)").fetchall()]
            for col, definition in [
                ("tutorial_score", "REAL DEFAULT 0"),
                ("upload_recency_score", "REAL DEFAULT 0"),
                ("selected", "INTEGER DEFAULT 0"),
                ("selected_at", "TEXT"),
            ]:
                if col not in cols:
                    conn.execute(f"ALTER TABLE scored_influencers ADD COLUMN {col} {definition}")

    def migrate_llm_scoring(self) -> None:
        """Add llm_score and llm_rationale columns to scored_influencers if missing."""
        with self._connect() as conn:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(scored_influencers)").fetchall()]
            for col, definition in [
                ("llm_score", "INTEGER DEFAULT NULL"),
                ("llm_rationale", "TEXT DEFAULT NULL"),
            ]:
                if col not in cols:
                    conn.execute(f"ALTER TABLE scored_influencers ADD COLUMN {col} {definition}")

    def select_influencers(self, channel_ids: list[str]) -> int:
        """Mark the given channel_ids as selected. Returns count updated."""
        if not channel_ids:
            return 0
        now = datetime.now().isoformat()
        placeholders = ",".join("?" * len(channel_ids))
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE scored_influencers SET selected = 1, selected_at = ? "
                f"WHERE channel_id IN ({placeholders})",
                [now, *channel_ids],
            )
            return cur.rowcount

    def deselect_influencers(self, channel_ids: list[str]) -> int:
        """Unmark the given channel_ids as selected. Returns count updated."""
        if not channel_ids:
            return 0
        placeholders = ",".join("?" * len(channel_ids))
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE scored_influencers SET selected = 0, selected_at = NULL "
                f"WHERE channel_id IN ({placeholders})",
                channel_ids,
            )
            return cur.rowcount

    def migrate_add_no_email(self) -> None:
        """Add no_email column to channels table if not present (safe to run repeatedly)."""
        with self._connect() as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(channels)").fetchall()]
            if "no_email" not in cols:
                conn.execute("ALTER TABLE channels ADD COLUMN no_email INTEGER DEFAULT 0")

    def mark_no_email(self, channel_ids: list[str]) -> int:
        """Flag channels where no contact email can be found. Returns count updated."""
        if not channel_ids:
            return 0
        placeholders = ",".join("?" * len(channel_ids))
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE channels SET no_email = 1 WHERE channel_id IN ({placeholders})",
                channel_ids,
            )
            return cur.rowcount

    def get_no_email_channel_ids(self) -> set[str]:
        """Return channel_ids permanently marked as no_email=1."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT channel_id FROM channels WHERE no_email = 1"
            ).fetchall()
        return {row["channel_id"] for row in rows}

    def migrate_add_contact_emails(self) -> None:
        """Add contact_emails JSON column to channels table if not present."""
        with self._connect() as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(channels)").fetchall()]
            if "contact_emails" not in cols:
                conn.execute("ALTER TABLE channels ADD COLUMN contact_emails TEXT")

    def migrate_add_contact_email(self) -> None:
        """Add contact_email column to existing tables if not present (safe to run repeatedly)."""
        with self._connect() as conn:
            for table in ("channels", "outreach_emails"):
                cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
                if "contact_email" not in cols:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN contact_email TEXT")

    def get_cached_channels(self, channel_ids: list[str], max_age_days: int = 7) -> dict[str, dict]:
        """Return enriched channel rows from DB that were updated within max_age_days.
        Key is channel_id. Only channels with a non-NULL last_updated_at are returned.
        """
        if not channel_ids:
            return {}
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
        placeholders = ",".join("?" * len(channel_ids))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM channels WHERE channel_id IN ({placeholders})"
                f" AND last_updated_at >= ? AND subscriber_count > 0",
                (*channel_ids, cutoff),
            ).fetchall()
        result = {}
        for row in rows:
            d = dict(row)
            # Deserialize JSON columns back to lists
            for col in ("keywords", "recent_video_titles"):
                try:
                    d[col] = json.loads(d[col]) if d[col] else []
                except (ValueError, TypeError):
                    d[col] = []
            result[d["channel_id"]] = d
        return result

    def get_cached_scores(self, channel_ids: list[str], max_age_days: int = 30) -> dict[str, dict]:
        """Return scored_influencer rows from DB scored within max_age_days.
        Key is channel_id.
        """
        if not channel_ids:
            return {}
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
        placeholders = ",".join("?" * len(channel_ids))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM scored_influencers WHERE channel_id IN ({placeholders})"
                f" AND scored_at >= ?",
                (*channel_ids, cutoff),
            ).fetchall()
        result = {}
        for row in rows:
            d = dict(row)
            try:
                d["niche_tags"] = json.loads(d["niche_tags"]) if d["niche_tags"] else []
            except (ValueError, TypeError):
                d["niche_tags"] = []
            result[d["channel_id"]] = d
        return result

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

    def upsert_channel(self, data: dict, touch_last_updated: bool = True) -> None:
        now = datetime.now().isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT first_seen_at FROM channels WHERE channel_id = ?",
                (data["channel_id"],)
            ).fetchone()
            first_seen = existing["first_seen_at"] if existing else now

            # When touch_last_updated=False (e.g. initial search save), preserve the
            # existing last_updated_at so the enrichment cache is not incorrectly
            # invalidated by the short-description search snippet.
            last_updated_clause = (
                "last_updated_at = excluded.last_updated_at"
                if touch_last_updated
                else "last_updated_at = COALESCE(channels.last_updated_at, excluded.last_updated_at)"
            )

            conn.execute(f"""
                INSERT INTO channels (
                    channel_id, channel_title, description,
                    subscriber_count, total_view_count, video_count,
                    country, default_language, keywords,
                    avg_views_per_video, avg_likes_per_video, avg_comments_per_video,
                    engagement_rate, upload_frequency_days, recent_video_titles,
                    search_keyword, first_seen_at, last_updated_at, contact_email,
                    contact_emails
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    {last_updated_clause},
                    contact_email = COALESCE(excluded.contact_email, channels.contact_email),
                    contact_emails = COALESCE(excluded.contact_emails, channels.contact_emails)
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
                data.get("contact_email"),
                json.dumps(data["contact_emails"]) if data.get("contact_emails") else None,
            ))

    def upsert_scored_influencer(self, data: dict) -> None:
        now = datetime.now().isoformat()
        breakdown = data.get("score_breakdown", {})
        llm_score = data.get("llm_score")       # None if not yet LLM-scored
        llm_rationale = data.get("llm_rationale")
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO scored_influencers (
                    channel_id, composite_score, engagement_score,
                    audience_size_score, relevance_score,
                    tutorial_score, upload_recency_score,
                    relevance_rationale, niche_tags, scored_at,
                    llm_score, llm_rationale
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    composite_score = excluded.composite_score,
                    engagement_score = excluded.engagement_score,
                    audience_size_score = excluded.audience_size_score,
                    relevance_score = excluded.relevance_score,
                    tutorial_score = excluded.tutorial_score,
                    upload_recency_score = excluded.upload_recency_score,
                    relevance_rationale = excluded.relevance_rationale,
                    niche_tags = excluded.niche_tags,
                    scored_at = excluded.scored_at,
                    llm_score = COALESCE(excluded.llm_score, scored_influencers.llm_score),
                    llm_rationale = COALESCE(excluded.llm_rationale, scored_influencers.llm_rationale)
            """, (
                data.get("channel_id"),
                data.get("composite_score", 0.0),
                breakdown.get("engagement", 0.0),
                breakdown.get("audience_size", 0.0),
                breakdown.get("relevance", 0.0),
                breakdown.get("tutorial", 0.0),
                breakdown.get("upload_recency", 0.0),
                data.get("relevance_rationale", ""),
                json.dumps(data.get("niche_tags", [])),
                now,
                llm_score,
                llm_rationale,
            ))

    def create_run(self, run_id: str, config: dict) -> None:
        """Insert a new run record at pipeline start."""
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO runs (
                    run_id, started_at, keywords, min_subscribers,
                    min_engagement_rate, max_results_per_keyword,
                    stop_after_filter, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running')
            """, (
                run_id,
                now,
                json.dumps(config.get("keywords", [])),
                config.get("min_subscribers", 0),
                config.get("min_engagement_rate", 0.0),
                config.get("max_results_per_keyword", 0),
                int(config.get("stop_after_filter", False)),
            ))

    def finish_run(self, run_id: str, stats: dict, status: str = "completed") -> None:
        """Update the run record with final stats and status."""
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute("""
                UPDATE runs SET
                    finished_at = ?,
                    total_found = ?,
                    total_deduped = ?,
                    total_pre_filtered = ?,
                    total_enriched = ?,
                    total_filtered = ?,
                    total_scored = ?,
                    total_emailed = ?,
                    error_count = ?,
                    status = ?
                WHERE run_id = ?
            """, (
                now,
                stats.get("total_found", 0),
                stats.get("total_deduped", 0),
                stats.get("total_pre_filtered", 0),
                stats.get("total_enriched", 0),
                stats.get("total_filtered", 0),
                stats.get("total_scored", 0),
                stats.get("total_emailed", 0),
                stats.get("error_count", 0),
                status,
                run_id,
            ))

    def add_log_entry(self, run_id: str, level: str, logger: str, message: str) -> None:
        """Insert a single log line into run_logs."""
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO run_logs (run_id, logged_at, level, logger, message) VALUES (?, ?, ?, ?, ?)",
                (run_id, now, level, logger, message),
            )

    def get_channels_by_keyword(self, keyword: str) -> list[dict]:
        """Return all channels discovered for a given search keyword."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM channels WHERE search_keyword = ?", (keyword,)
            ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            for col in ("keywords", "recent_video_titles"):
                try:
                    d[col] = json.loads(d[col]) if d[col] else []
                except (ValueError, TypeError):
                    d[col] = []
            result.append(d)
        return result

    def get_searched_keywords(self) -> set[str]:
        """Return all keywords that have been successfully searched."""
        with self._connect() as conn:
            rows = conn.execute("SELECT keyword FROM searched_keywords").fetchall()
        return {row["keyword"] for row in rows}

    def clear_searched_keywords(self) -> None:
        """Clear the search resume cache (called at the start of a fresh run)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM searched_keywords")

    def mark_keyword_searched(self, keyword: str, channels_found: int) -> None:
        """Record that a keyword was successfully searched."""
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO searched_keywords (keyword, searched_at, channels_found)
                VALUES (?, ?, ?)
                ON CONFLICT(keyword) DO UPDATE SET
                    searched_at = excluded.searched_at,
                    channels_found = excluded.channels_found
            """, (keyword, now, channels_found))

    def mark_emails_sent(self, channel_ids: list[str]) -> None:
        """Set sent_at timestamp on outreach_emails rows that were successfully delivered."""
        if not channel_ids:
            return
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.executemany(
                "UPDATE outreach_emails SET sent_at = ? WHERE channel_id = ?",
                [(now, cid) for cid in channel_ids],
            )

    def mark_channels_filtered(self, channel_ids: list[str]) -> None:
        """Set passed_filter_at timestamp for channels that passed all filters."""
        if not channel_ids:
            return
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.executemany(
                "UPDATE channels SET passed_filter_at = ? WHERE channel_id = ?",
                [(now, cid) for cid in channel_ids],
            )

    def import_promoters_from_csv(self, csv_path: str) -> tuple[int, int, int]:
        """Import affiliate promoter emails from a CSV file.

        Only the 'email' column is read. Values are validated against a basic
        email regex and lowercased before insert. Duplicate emails are skipped.

        Returns (inserted, skipped_duplicates, skipped_invalid).
        """
        import csv
        import re
        _email_re = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

        inserted = skipped_dup = skipped_invalid = 0
        now = datetime.now().isoformat()

        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        with self._connect() as conn:
            for row in rows:
                raw = (row.get("email") or "").strip().lower()
                if not _email_re.match(raw):
                    skipped_invalid += 1
                    continue
                try:
                    conn.execute(
                        "INSERT INTO affiliate_promoters (email, imported_at) VALUES (?, ?)",
                        (raw, now),
                    )
                    inserted += 1
                except sqlite3.IntegrityError:
                    skipped_dup += 1

        return inserted, skipped_dup, skipped_invalid

    def get_promoter_emails(self) -> set[str]:
        """Return the set of all known affiliate promoter emails (lowercased)."""
        with self._connect() as conn:
            rows = conn.execute("SELECT email FROM affiliate_promoters").fetchall()
        return {row["email"] for row in rows}

    def upsert_email(self, data: dict) -> None:
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO outreach_emails (
                    channel_id, subject_line, email_body,
                    personalization_hooks, contact_email, generated_at, sent_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(channel_id) DO UPDATE SET
                    subject_line = excluded.subject_line,
                    email_body = excluded.email_body,
                    personalization_hooks = excluded.personalization_hooks,
                    contact_email = excluded.contact_email,
                    generated_at = excluded.generated_at
            """, (
                data.get("channel_id"),
                data.get("subject_line", ""),
                data.get("email_body", ""),
                json.dumps(data.get("personalization_hooks", [])),
                data.get("contact_email"),
                now,
            ))
