"""Tests for src/db/database.py — uses a temp SQLite file per test (no mocking)."""
import json
import sqlite3
import pytest
from src.db.database import Database


@pytest.fixture
def db(tmp_path):
    d = Database(db_path=str(tmp_path / "test.db"))
    d.init_db()
    return d


def _channel_data(channel_id="UC_test_001"):
    return {
        "channel_id": channel_id,
        "channel_title": "Analytics Pro",
        "description": "Google Analytics and GA4 tutorials.",
        "subscriber_count": 25_000,
        "total_view_count": 500_000,
        "video_count": 100,
        "country": "US",
        "default_language": "en",
        "keywords": ["google analytics", "ga4"],
        "avg_views_per_video": 5_000.0,
        "avg_likes_per_video": 200.0,
        "avg_comments_per_video": 50.0,
        "engagement_rate": 3.5,
        "upload_frequency_days": 7.0,
        "recent_video_titles": ["GA4 Guide", "Attribution Models"],
        "search_keyword": "google analytics",
    }


def _score_data(channel_id="UC_test_001"):
    return {
        "channel_id": channel_id,
        "composite_score": 52.5,
        "score_breakdown": {
            "engagement": 27.5,
            "audience_size": 15.0,
            "relevance": 10.0,
        },
        "relevance_rationale": "Good fit for Windsor.ai.",
        "niche_tags": ["Google Analytics", "Marketing Analytics"],
    }


# ---------------------------------------------------------------------------
# channels table
# ---------------------------------------------------------------------------

def test_upsert_and_retrieve_channel(db):
    db.upsert_channel(_channel_data())
    cached = db.get_cached_channels(["UC_test_001"], max_age_days=7)
    assert "UC_test_001" in cached
    assert cached["UC_test_001"]["channel_title"] == "Analytics Pro"


def test_upsert_preserves_first_seen_at(db):
    db.upsert_channel(_channel_data())
    first_seen = db.get_cached_channels(["UC_test_001"])["UC_test_001"]["first_seen_at"]

    # Upsert again
    data = _channel_data()
    data["channel_title"] = "Updated Title"
    db.upsert_channel(data)

    after = db.get_cached_channels(["UC_test_001"])["UC_test_001"]
    assert after["first_seen_at"] == first_seen
    assert after["channel_title"] == "Updated Title"


def test_get_cached_channels_max_age_filters_old_records(db):
    db.upsert_channel(_channel_data())
    # Manually backdate last_updated_at to 10 days ago
    conn = sqlite3.connect(db.db_path)
    conn.execute(
        "UPDATE channels SET last_updated_at = '2020-01-01T00:00:00+00:00' WHERE channel_id = ?",
        ("UC_test_001",),
    )
    conn.commit()
    conn.close()

    cached = db.get_cached_channels(["UC_test_001"], max_age_days=1)
    assert "UC_test_001" not in cached


def test_get_cached_channels_deserializes_json_columns(db):
    db.upsert_channel(_channel_data())
    cached = db.get_cached_channels(["UC_test_001"])["UC_test_001"]
    assert isinstance(cached["keywords"], list)
    assert isinstance(cached["recent_video_titles"], list)
    assert "google analytics" in cached["keywords"]


def test_get_cached_channels_returns_empty_for_missing_id(db):
    cached = db.get_cached_channels(["UC_does_not_exist"])
    assert cached == {}


def test_get_cached_channels_empty_list(db):
    cached = db.get_cached_channels([])
    assert cached == {}


# ---------------------------------------------------------------------------
# scored_influencers table
# ---------------------------------------------------------------------------

def test_upsert_and_retrieve_scored_influencer(db):
    # Must insert channel first (FK constraint)
    db.upsert_channel(_channel_data())
    db.upsert_scored_influencer(_score_data())

    cached = db.get_cached_scores(["UC_test_001"], max_age_days=30)
    assert "UC_test_001" in cached
    assert cached["UC_test_001"]["composite_score"] == pytest.approx(52.5)


def test_get_cached_scores_max_age_filters_old(db):
    db.upsert_channel(_channel_data())
    db.upsert_scored_influencer(_score_data())

    # Backdate scored_at
    conn = sqlite3.connect(db.db_path)
    conn.execute(
        "UPDATE scored_influencers SET scored_at = '2020-01-01T00:00:00+00:00' WHERE channel_id = ?",
        ("UC_test_001",),
    )
    conn.commit()
    conn.close()

    cached = db.get_cached_scores(["UC_test_001"], max_age_days=1)
    assert "UC_test_001" not in cached


def test_get_cached_scores_deserializes_niche_tags(db):
    db.upsert_channel(_channel_data())
    db.upsert_scored_influencer(_score_data())

    cached = db.get_cached_scores(["UC_test_001"])["UC_test_001"]
    assert isinstance(cached["niche_tags"], list)
    assert "Google Analytics" in cached["niche_tags"]


def test_get_cached_scores_returns_empty_for_missing(db):
    cached = db.get_cached_scores(["UC_does_not_exist"])
    assert cached == {}


def test_get_cached_scores_empty_list(db):
    assert db.get_cached_scores([]) == {}


# ---------------------------------------------------------------------------
# mark_channels_filtered
# ---------------------------------------------------------------------------

def test_mark_channels_filtered_sets_timestamp(db):
    db.upsert_channel(_channel_data())
    db.mark_channels_filtered(["UC_test_001"])

    conn = sqlite3.connect(db.db_path)
    row = conn.execute(
        "SELECT passed_filter_at FROM channels WHERE channel_id = ?", ("UC_test_001",)
    ).fetchone()
    conn.close()
    assert row[0] is not None


def test_mark_channels_filtered_empty_list_no_error(db):
    db.mark_channels_filtered([])  # should not raise


# ---------------------------------------------------------------------------
# outreach_emails — get_emailed_channel_ids
# ---------------------------------------------------------------------------

def _insert_email(db, channel_id, sent_at=None, contact_email=None):
    """Helper to insert an email record directly via SQL."""
    conn = sqlite3.connect(db.db_path)
    conn.execute(
        "INSERT INTO outreach_emails (channel_id, subject_line, email_body, personalization_hooks, contact_email, generated_at, sent_at)"
        " VALUES (?, ?, ?, ?, ?, datetime('now'), ?)",
        (channel_id, "Subject", "Body", "[]", contact_email, sent_at),
    )
    conn.commit()
    conn.close()


def test_get_emailed_channel_ids_excludes_unsent(db):
    db.upsert_channel(_channel_data())
    _insert_email(db, "UC_test_001", sent_at=None)

    result = db.get_emailed_channel_ids()
    assert "UC_test_001" not in result


def test_get_emailed_channel_ids_includes_sent(db):
    db.upsert_channel(_channel_data())
    _insert_email(db, "UC_test_001", sent_at="2026-04-01T10:00:00")

    result = db.get_emailed_channel_ids()
    assert "UC_test_001" in result


def test_get_emailed_channel_ids_empty_table(db):
    result = db.get_emailed_channel_ids()
    assert result == set()


# ---------------------------------------------------------------------------
# runs + run_logs tables
# ---------------------------------------------------------------------------

def test_create_and_finish_run(db):
    run_id = "test-run-001"
    db.create_run(run_id, {
        "keywords": ["google analytics"],
        "min_subscribers": 5_000,
        "min_engagement_rate": 1.0,
        "max_results_per_keyword": 10,
        "stop_after_filter": False,
    })
    db.finish_run(run_id, {
        "total_found": 20,
        "total_scored": 5,
    }, status="completed")

    conn = sqlite3.connect(db.db_path)
    row = conn.execute("SELECT status, total_found, finished_at FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    conn.close()
    assert row[0] == "completed"
    assert row[1] == 20
    assert row[2] is not None


def test_add_log_entry_stored(db):
    run_id = "test-run-002"
    db.create_run(run_id, {})
    db.add_log_entry(run_id, "INFO", "src.nodes.test", "Pipeline started")

    conn = sqlite3.connect(db.db_path)
    row = conn.execute(
        "SELECT level, logger, message FROM run_logs WHERE run_id = ?", (run_id,)
    ).fetchone()
    conn.close()
    assert row[0] == "INFO"
    assert row[1] == "src.nodes.test"
    assert row[2] == "Pipeline started"


def test_finish_run_sets_status_failed(db):
    run_id = "test-run-003"
    db.create_run(run_id, {})
    db.finish_run(run_id, {}, status="failed")

    conn = sqlite3.connect(db.db_path)
    row = conn.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    conn.close()
    assert row[0] == "failed"


# ---------------------------------------------------------------------------
# contact_email — channels table
# ---------------------------------------------------------------------------

def test_upsert_channel_stores_contact_email(db):
    data = _channel_data()
    data["contact_email"] = "creator@example.com"
    db.upsert_channel(data)

    conn = sqlite3.connect(db.db_path)
    row = conn.execute(
        "SELECT contact_email FROM channels WHERE channel_id = ?", ("UC_test_001",)
    ).fetchone()
    conn.close()
    assert row[0] == "creator@example.com"


def test_upsert_channel_contact_email_defaults_to_none(db):
    db.upsert_channel(_channel_data())  # no contact_email key

    conn = sqlite3.connect(db.db_path)
    row = conn.execute(
        "SELECT contact_email FROM channels WHERE channel_id = ?", ("UC_test_001",)
    ).fetchone()
    conn.close()
    assert row[0] is None


def test_upsert_channel_preserves_existing_contact_email_on_update(db):
    data = _channel_data()
    data["contact_email"] = "original@example.com"
    db.upsert_channel(data)

    # Upsert again without contact_email — should not overwrite
    db.upsert_channel(_channel_data())

    conn = sqlite3.connect(db.db_path)
    row = conn.execute(
        "SELECT contact_email FROM channels WHERE channel_id = ?", ("UC_test_001",)
    ).fetchone()
    conn.close()
    assert row[0] == "original@example.com"


# ---------------------------------------------------------------------------
# contact_email — outreach_emails table
# ---------------------------------------------------------------------------

def test_upsert_email_stores_contact_email(db):
    db.upsert_channel(_channel_data())
    db.upsert_email({
        "channel_id": "UC_test_001",
        "subject_line": "Hi",
        "email_body": "Body",
        "personalization_hooks": [],
        "contact_email": "creator@example.com",
    })

    conn = sqlite3.connect(db.db_path)
    row = conn.execute(
        "SELECT contact_email FROM outreach_emails WHERE channel_id = ?", ("UC_test_001",)
    ).fetchone()
    conn.close()
    assert row[0] == "creator@example.com"


def test_upsert_email_contact_email_none_when_not_found(db):
    db.upsert_channel(_channel_data())
    db.upsert_email({
        "channel_id": "UC_test_001",
        "subject_line": "Hi",
        "email_body": "Body",
        "personalization_hooks": [],
        "contact_email": None,
    })

    conn = sqlite3.connect(db.db_path)
    row = conn.execute(
        "SELECT contact_email FROM outreach_emails WHERE channel_id = ?", ("UC_test_001",)
    ).fetchone()
    conn.close()
    assert row[0] is None


# ---------------------------------------------------------------------------
# migrate_add_contact_email
# ---------------------------------------------------------------------------

def test_migration_adds_column_to_existing_db(tmp_path):
    """Simulate an old DB without contact_email — migration should add it."""
    db = Database(db_path=str(tmp_path / "old.db"))
    db.init_db()

    # Drop the contact_email column to simulate an old DB
    conn = sqlite3.connect(db.db_path)
    conn.execute("CREATE TABLE channels_old AS SELECT channel_id, channel_title FROM channels")
    conn.execute("DROP TABLE channels")
    conn.execute("ALTER TABLE channels_old RENAME TO channels")
    conn.commit()
    conn.close()

    # Verify column is missing
    conn = sqlite3.connect(db.db_path)
    cols_before = [r[1] for r in conn.execute("PRAGMA table_info(channels)").fetchall()]
    conn.close()
    assert "contact_email" not in cols_before

    # Run migration
    db.migrate_add_contact_email()

    conn = sqlite3.connect(db.db_path)
    cols_after = [r[1] for r in conn.execute("PRAGMA table_info(channels)").fetchall()]
    conn.close()
    assert "contact_email" in cols_after


def test_migration_is_idempotent(db):
    """Running migration twice should not raise."""
    db.migrate_add_contact_email()
    db.migrate_add_contact_email()  # second call must not error


# ---------------------------------------------------------------------------
# affiliate_promoters table
# ---------------------------------------------------------------------------

def test_import_promoters_inserts_valid_emails(db, tmp_path):
    csv_file = tmp_path / "promoters.csv"
    csv_file.write_text("email,name\nhello@example.com,Alice\nworld@domain.org,Bob\n")

    inserted, skipped_dup, skipped_invalid = db.import_promoters_from_csv(str(csv_file))

    assert inserted == 2
    assert skipped_dup == 0
    assert skipped_invalid == 0


def test_import_promoters_lowercases_emails(db, tmp_path):
    csv_file = tmp_path / "promoters.csv"
    csv_file.write_text("email\nHELLO@EXAMPLE.COM\n")

    db.import_promoters_from_csv(str(csv_file))

    emails = db.get_promoter_emails()
    assert "hello@example.com" in emails
    assert "HELLO@EXAMPLE.COM" not in emails


def test_import_promoters_skips_invalid_emails(db, tmp_path):
    csv_file = tmp_path / "promoters.csv"
    csv_file.write_text("email\nnot-an-email\n@missinglocal.com\nvalid@example.com\n")

    inserted, skipped_dup, skipped_invalid = db.import_promoters_from_csv(str(csv_file))

    assert inserted == 1
    assert skipped_invalid == 2


def test_import_promoters_skips_duplicates(db, tmp_path):
    csv_file = tmp_path / "promoters.csv"
    csv_file.write_text("email\nduplicate@example.com\n")

    db.import_promoters_from_csv(str(csv_file))
    inserted, skipped_dup, skipped_invalid = db.import_promoters_from_csv(str(csv_file))

    assert inserted == 0
    assert skipped_dup == 1


def test_import_promoters_skips_injection_attempt(db, tmp_path):
    csv_file = tmp_path / "promoters.csv"
    csv_file.write_text('email,company_name\nvalid@example.com,Legit Corp\nhacker@x.com,"<h1>HACKED</h1>${{7*7}}"\n')

    inserted, _, skipped_invalid = db.import_promoters_from_csv(str(csv_file))

    # Both emails are structurally valid — injection is in a different column (ignored)
    assert inserted == 2
    assert skipped_invalid == 0
    emails = db.get_promoter_emails()
    assert "valid@example.com" in emails
    assert "hacker@x.com" in emails


def test_get_promoter_emails_returns_empty_set_when_none(db):
    assert db.get_promoter_emails() == set()


def test_get_promoter_emails_returns_all_imported(db, tmp_path):
    csv_file = tmp_path / "promoters.csv"
    csv_file.write_text("email\nalpha@example.com\nbeta@example.com\n")

    db.import_promoters_from_csv(str(csv_file))
    emails = db.get_promoter_emails()

    assert emails == {"alpha@example.com", "beta@example.com"}
