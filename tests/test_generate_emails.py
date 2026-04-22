"""Tests for email extraction in src/nodes/generate_emails.py."""
import pytest
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
