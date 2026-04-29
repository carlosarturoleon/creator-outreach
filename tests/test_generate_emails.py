"""Tests for email extraction and promoter filtering in src/nodes/generate_emails.py."""
import pytest
from unittest.mock import MagicMock, patch
from src.nodes.generate_emails import _extract_email


# ---------------------------------------------------------------------------
# _extract_email
# ---------------------------------------------------------------------------

def test_extracts_plain_email():
    assert _extract_email("Contact me at hello@example.com for business.") == "hello@example.com"


def test_extracts_business_email_prefix():
    assert _extract_email("business@mysite.co.uk") == "business@mysite.co.uk"


def test_extracts_email_with_dots_and_plus():
    assert _extract_email("Reach me: first.last+tag@domain.org") == "first.last+tag@domain.org"


def test_returns_first_email_when_multiple():
    text = "primary@one.com or backup@two.com"
    assert _extract_email(text) == "primary@one.com"


def test_returns_none_when_no_email():
    assert _extract_email("No contact info here.") is None


def test_returns_none_for_empty_string():
    assert _extract_email("") is None


def test_returns_none_for_none_input():
    assert _extract_email(None) is None


def test_extracts_from_multiline_description():
    desc = (
        "Welcome to my channel!\n"
        "I cover Google Analytics and GA4.\n"
        "For sponsorships: sponsor@analytics.pro\n"
        "Subscribe for weekly tutorials."
    )
    assert _extract_email(desc) == "sponsor@analytics.pro"


def test_ignores_invalid_email_no_tld():
    # Missing TLD — should not match
    assert _extract_email("bad@nodot") is None


def test_extracts_email_embedded_in_url_like_text():
    # Should still find the email even with surrounding punctuation
    result = _extract_email("(contact@example.com)")
    assert result == "contact@example.com"


# ---------------------------------------------------------------------------
# generate_emails node — promoter filtering
# ---------------------------------------------------------------------------

def _make_influencer(channel_id, email):
    """Return a minimal influencer dict with a contact email in the description."""
    return {
        "channel_id": channel_id,
        "channel_title": f"Channel {channel_id}",
        "composite_score": 60.0,
        "llm_rationale": "Good fit.",
        "niche_tags": ["analytics"],
        "description": f"Email me at {email}",
    }


def _build_state(influencers):
    enriched = [
        {**inf, "description": inf["description"]}
        for inf in influencers
    ]
    return {
        "scored_influencers": influencers,
        "enriched_channels": enriched,
        "run_id": "test-run",
    }


@patch("src.nodes.generate_emails.Database")
@patch("src.nodes.generate_emails.submit_batch", return_value="batch-123")
@patch("src.nodes.generate_emails.wait_for_batch")
@patch("src.nodes.generate_emails.fetch_email_results")
@patch("src.nodes.generate_emails.build_email_requests", return_value=[])
def test_promoter_email_is_excluded_from_batch(
    mock_build, mock_fetch, mock_wait, mock_submit, mock_db_cls
):
    """Influencers whose contact email is a known promoter must not enter the batch."""
    promoter_email = "promoter@example.com"
    non_promoter_email = "newbie@example.com"

    mock_db = MagicMock()
    mock_db.get_promoter_emails.return_value = {promoter_email}
    mock_db_cls.return_value = mock_db

    mock_fetch.return_value = {
        "UC_new": {
            "success": True,
            "subject_line": "Join Windsor.ai",
            "email_body": "Hi there",
            "personalization_hooks": [],
        }
    }

    influencers = [
        _make_influencer("UC_promoter", promoter_email),
        _make_influencer("UC_new", non_promoter_email),
    ]
    state = _build_state(influencers)

    from src.nodes.generate_emails import generate_emails
    result = generate_emails(state)

    # build_email_requests must only receive the non-promoter
    called_influencers = mock_build.call_args[1]["influencers"]
    channel_ids = [inf["channel_id"] for inf in called_influencers]
    assert "UC_promoter" not in channel_ids
    assert "UC_new" in channel_ids


@patch("src.nodes.generate_emails.Database")
def test_all_promoters_skips_batch_entirely(mock_db_cls):
    """If all influencers are promoters, the batch must not be submitted."""
    promoter_email = "promoter@example.com"

    mock_db = MagicMock()
    mock_db.get_promoter_emails.return_value = {promoter_email}
    mock_db_cls.return_value = mock_db

    influencers = [_make_influencer("UC_promoter", promoter_email)]
    state = _build_state(influencers)

    with patch("src.nodes.generate_emails.submit_batch") as mock_submit:
        from src.nodes.generate_emails import generate_emails
        result = generate_emails(state)
        mock_submit.assert_not_called()

    assert result["outreach_emails"] == []


@patch("src.nodes.generate_emails.Database")
@patch("src.nodes.generate_emails.submit_batch", return_value="batch-123")
@patch("src.nodes.generate_emails.wait_for_batch")
@patch("src.nodes.generate_emails.fetch_email_results")
@patch("src.nodes.generate_emails.build_email_requests", return_value=[])
def test_no_promoters_in_db_passes_all_through(
    mock_build, mock_fetch, mock_wait, mock_submit, mock_db_cls
):
    """When the promoter table is empty, no influencer is filtered out."""
    mock_db = MagicMock()
    mock_db.get_promoter_emails.return_value = set()
    mock_db_cls.return_value = mock_db

    mock_fetch.return_value = {
        "UC_001": {
            "success": True,
            "subject_line": "Hi",
            "email_body": "Body",
            "personalization_hooks": [],
        }
    }

    influencers = [_make_influencer("UC_001", "creator@example.com")]
    state = _build_state(influencers)

    from src.nodes.generate_emails import generate_emails
    generate_emails(state)

    called_influencers = mock_build.call_args[1]["influencers"]
    assert any(inf["channel_id"] == "UC_001" for inf in called_influencers)
