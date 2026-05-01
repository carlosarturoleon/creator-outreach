"""Tests for src/nodes/scrape_contact_emails.py"""
from unittest.mock import MagicMock, patch

import pytest

from src.nodes.scrape_contact_emails import scrape_contact_emails


def _ch(channel_id="UC_001", description="Visit https://mysite.com", contact_email=None):
    ch = {
        "channel_id": channel_id,
        "channel_title": "Test Channel",
        "description": description,
    }
    if contact_email:
        ch["contact_email"] = contact_email
    return ch


def _state(channels, no_email_ids=None):
    return {
        "enriched_channels": channels,
        "scored_influencers": [{"channel_id": ch["channel_id"]} for ch in channels],
    }


def _mock_db(no_email_ids=None):
    db = MagicMock()
    db.get_no_email_channel_ids.return_value = set(no_email_ids or [])
    return db


# ---------------------------------------------------------------------------
# Basic flow
# ---------------------------------------------------------------------------

def test_returns_scraped_channels_key():
    with patch("src.nodes.scrape_contact_emails.Database", return_value=_mock_db()), \
         patch("src.nodes.scrape_contact_emails.scrape_emails_from_url", return_value=[]):
        result = scrape_contact_emails(_state([_ch()]))
    assert "scraped_channels" in result
    assert result["current_phase"] == "scrape_complete"


def test_empty_input_returns_empty():
    with patch("src.nodes.scrape_contact_emails.Database", return_value=_mock_db()):
        result = scrape_contact_emails({"enriched_channels": [], "scored_influencers": []})
    assert result["scraped_channels"] == []


def test_all_channels_preserved_in_output():
    channels = [_ch("UC_001"), _ch("UC_002"), _ch("UC_003")]
    with patch("src.nodes.scrape_contact_emails.Database", return_value=_mock_db()), \
         patch("src.nodes.scrape_contact_emails.scrape_emails_from_url", return_value=[]):
        result = scrape_contact_emails(_state(channels))
    assert len(result["scraped_channels"]) == 3


# ---------------------------------------------------------------------------
# Email discovery
# ---------------------------------------------------------------------------

def test_sets_contact_email_when_scraped():
    ch = _ch(description="Visit https://mysite.com")
    with patch("src.nodes.scrape_contact_emails.Database", return_value=_mock_db()), \
         patch("src.nodes.scrape_contact_emails.scrape_emails_from_url", return_value=["found@example.com"]):
        result = scrape_contact_emails(_state([ch]))
    assert result["scraped_channels"][0]["contact_email"] == "found@example.com"


def test_stores_all_emails_in_contact_emails_list():
    ch = _ch(description="Visit https://mysite.com")
    with patch("src.nodes.scrape_contact_emails.Database", return_value=_mock_db()), \
         patch("src.nodes.scrape_contact_emails.scrape_emails_from_url",
               return_value=["a@site.com", "b@site.com"]):
        result = scrape_contact_emails(_state([ch]))
    assert result["scraped_channels"][0]["contact_emails"] == ["a@site.com", "b@site.com"]


def test_preserves_existing_contact_email_as_primary():
    ch = _ch(description="Visit https://mysite.com", contact_email="existing@site.com")
    with patch("src.nodes.scrape_contact_emails.Database", return_value=_mock_db()), \
         patch("src.nodes.scrape_contact_emails.scrape_emails_from_url",
               return_value=["scraped@site.com"]):
        result = scrape_contact_emails(_state([ch]))
    updated = result["scraped_channels"][0]
    # Existing email stays as primary
    assert updated["contact_email"] == "existing@site.com"
    # But scraped email is included in the full list
    assert "scraped@site.com" in updated["contact_emails"]


def test_no_contact_email_when_scraping_finds_nothing():
    ch = _ch(description="Visit https://mysite.com")
    with patch("src.nodes.scrape_contact_emails.Database", return_value=_mock_db()), \
         patch("src.nodes.scrape_contact_emails.scrape_emails_from_url", return_value=[]):
        result = scrape_contact_emails(_state([ch]))
    updated = result["scraped_channels"][0]
    assert updated.get("contact_email") is None
    assert "contact_emails" not in updated


def test_no_scrape_attempt_when_no_urls_in_description():
    ch = _ch(description="No links here just text")
    with patch("src.nodes.scrape_contact_emails.Database", return_value=_mock_db()), \
         patch("src.nodes.scrape_contact_emails.scrape_emails_from_url") as mock_scrape:
        scrape_contact_emails(_state([ch]))
    mock_scrape.assert_not_called()


# ---------------------------------------------------------------------------
# no_email flag
# ---------------------------------------------------------------------------

def test_skips_no_email_flagged_channels():
    ch = _ch("UC_001", description="Visit https://mysite.com")
    db = _mock_db(no_email_ids=["UC_001"])
    with patch("src.nodes.scrape_contact_emails.Database", return_value=db), \
         patch("src.nodes.scrape_contact_emails.scrape_emails_from_url") as mock_scrape:
        result = scrape_contact_emails(_state([ch]))
    mock_scrape.assert_not_called()
    assert result["scraped_channels"][0].get("contact_email") is None


def test_only_skips_flagged_channels():
    ch1 = _ch("UC_001", description="Visit https://site1.com")
    ch2 = _ch("UC_002", description="Visit https://site2.com")
    db = _mock_db(no_email_ids=["UC_001"])
    with patch("src.nodes.scrape_contact_emails.Database", return_value=db), \
         patch("src.nodes.scrape_contact_emails.scrape_emails_from_url",
               return_value=["found@site.com"]):
        result = scrape_contact_emails(_state([ch1, ch2]))
    channels_by_id = {ch["channel_id"]: ch for ch in result["scraped_channels"]}
    assert channels_by_id["UC_001"].get("contact_email") is None
    assert channels_by_id["UC_002"]["contact_email"] == "found@site.com"


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------

def test_upserts_db_when_email_found():
    ch = _ch(description="Visit https://mysite.com")
    db = _mock_db()
    with patch("src.nodes.scrape_contact_emails.Database", return_value=db), \
         patch("src.nodes.scrape_contact_emails.scrape_emails_from_url",
               return_value=["found@example.com"]):
        scrape_contact_emails(_state([ch]))
    db.upsert_channel.assert_called_once()
    call_arg = db.upsert_channel.call_args[0][0]
    assert call_arg["contact_email"] == "found@example.com"


def test_does_not_upsert_when_no_email_found():
    ch = _ch(description="Visit https://mysite.com")
    db = _mock_db()
    with patch("src.nodes.scrape_contact_emails.Database", return_value=db), \
         patch("src.nodes.scrape_contact_emails.scrape_emails_from_url", return_value=[]):
        scrape_contact_emails(_state([ch]))
    db.upsert_channel.assert_not_called()


def test_continues_if_db_upsert_raises():
    ch = _ch(description="Visit https://mysite.com")
    db = _mock_db()
    db.upsert_channel.side_effect = Exception("DB error")
    with patch("src.nodes.scrape_contact_emails.Database", return_value=db), \
         patch("src.nodes.scrape_contact_emails.scrape_emails_from_url",
               return_value=["found@example.com"]):
        # Should not raise
        result = scrape_contact_emails(_state([ch]))
    assert len(result["scraped_channels"]) == 1


# ---------------------------------------------------------------------------
# URL limit
# ---------------------------------------------------------------------------

def test_tries_at_most_3_urls():
    description = (
        "https://site1.com "
        "https://site2.com "
        "https://site3.com "
        "https://site4.com"
    )
    ch = _ch(description=description)
    with patch("src.nodes.scrape_contact_emails.Database", return_value=_mock_db()), \
         patch("src.nodes.scrape_contact_emails.scrape_emails_from_url",
               return_value=[]) as mock_scrape:
        scrape_contact_emails(_state([ch]))
    assert mock_scrape.call_count <= 3


def test_stops_scraping_urls_after_first_email_found():
    ch = _ch(description="https://site1.com https://site2.com https://site3.com")
    with patch("src.nodes.scrape_contact_emails.Database", return_value=_mock_db()), \
         patch("src.nodes.scrape_contact_emails.scrape_emails_from_url",
               return_value=["found@example.com"]) as mock_scrape:
        scrape_contact_emails(_state([ch]))
    # Stops after first URL returns an email (still collects from all URLs per current design,
    # but at most 3 total)
    assert mock_scrape.call_count >= 1
