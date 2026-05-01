"""Tests for src/tools/web_scraper.py"""
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.tools.web_scraper import (
    extract_urls_from_text,
    scrape_emails_from_url,
)


# ---------------------------------------------------------------------------
# extract_urls_from_text
# ---------------------------------------------------------------------------

def test_extracts_https_url():
    urls = extract_urls_from_text("Visit https://mysite.com/contact for info")
    assert urls == ["https://mysite.com/contact"]


def test_extracts_multiple_urls_up_to_3():
    text = (
        "https://site1.com "
        "https://site2.com "
        "https://site3.com "
        "https://site4.com"
    )
    urls = extract_urls_from_text(text)
    assert len(urls) == 3
    assert "https://site4.com" not in urls


def test_skips_youtube_urls():
    text = "Watch https://youtube.com/watch?v=abc or visit https://mysite.com"
    urls = extract_urls_from_text(text)
    assert urls == ["https://mysite.com"]


def test_skips_social_media_domains():
    text = (
        "Follow https://twitter.com/me "
        "https://instagram.com/me "
        "https://tiktok.com/@me "
        "https://mysite.com"
    )
    urls = extract_urls_from_text(text)
    assert urls == ["https://mysite.com"]


def test_strips_trailing_punctuation():
    urls = extract_urls_from_text("Site: https://mysite.com. More info here.")
    assert urls == ["https://mysite.com"]


def test_strips_trailing_parenthesis():
    urls = extract_urls_from_text("(see https://mysite.com)")
    assert urls == ["https://mysite.com"]


def test_deduplicates_same_url():
    text = "https://mysite.com and https://mysite.com again"
    urls = extract_urls_from_text(text)
    assert urls == ["https://mysite.com"]


def test_returns_empty_for_no_urls():
    assert extract_urls_from_text("No links here, just text.") == []


def test_returns_empty_for_empty_string():
    assert extract_urls_from_text("") == []


def test_returns_empty_for_none():
    assert extract_urls_from_text(None) == []


def test_extracts_linktree_url():
    text = "My links: https://linktr.ee/somehandle"
    urls = extract_urls_from_text(text)
    assert urls == ["https://linktr.ee/somehandle"]


# ---------------------------------------------------------------------------
# scrape_emails_from_url — generic sites
# ---------------------------------------------------------------------------

def _make_response(html: str, status_code: int = 200):
    resp = MagicMock(spec=httpx.Response)
    resp.text = html
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    return resp


def test_scrape_generic_finds_email_in_mailto():
    html = '<html><body><a href="mailto:hello@mysite.com">email me</a></body></html>'
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _make_response(html)

    with patch("src.tools.web_scraper.httpx.Client", return_value=mock_client):
        emails = scrape_emails_from_url("https://mysite.com/contact")

    assert "hello@mysite.com" in emails


def test_scrape_generic_finds_email_in_plain_text():
    html = "<html><body>Contact us at support@company.io for help.</body></html>"
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _make_response(html)

    with patch("src.tools.web_scraper.httpx.Client", return_value=mock_client):
        emails = scrape_emails_from_url("https://mysite.com")

    assert "support@company.io" in emails


def test_scrape_generic_returns_multiple_emails():
    html = (
        '<html><body>'
        '<a href="mailto:primary@site.com">primary</a> '
        'or secondary@site.com'
        '</body></html>'
    )
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _make_response(html)

    with patch("src.tools.web_scraper.httpx.Client", return_value=mock_client):
        emails = scrape_emails_from_url("https://mysite.com")

    assert len(emails) == 2
    assert "primary@site.com" in emails
    assert "secondary@site.com" in emails


def test_scrape_returns_empty_on_timeout():
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.side_effect = httpx.TimeoutException("timeout")

    with patch("src.tools.web_scraper.httpx.Client", return_value=mock_client):
        emails = scrape_emails_from_url("https://mysite.com")

    assert emails == []


def test_scrape_returns_empty_on_http_error():
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    resp = _make_response("", 404)
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "404", request=MagicMock(), response=resp
    )
    mock_client.get.return_value = resp

    with patch("src.tools.web_scraper.httpx.Client", return_value=mock_client):
        emails = scrape_emails_from_url("https://mysite.com")

    assert emails == []


def test_scrape_returns_empty_on_connect_error():
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.side_effect = httpx.ConnectError("refused")

    with patch("src.tools.web_scraper.httpx.Client", return_value=mock_client):
        emails = scrape_emails_from_url("https://unreachable.example.com")

    assert emails == []


def test_scrape_returns_empty_when_no_email_on_page():
    html = "<html><body>No contact info here at all.</body></html>"
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _make_response(html)

    with patch("src.tools.web_scraper.httpx.Client", return_value=mock_client):
        emails = scrape_emails_from_url("https://mysite.com")

    assert emails == []


# ---------------------------------------------------------------------------
# scrape_emails_from_url — Linktree routing
# ---------------------------------------------------------------------------

def test_linktree_url_finds_email_in_next_data():
    next_data_json = '{"props":{"pageProps":{"account":{"email":"creator@mysite.com"}}}}'
    html = f'<html><head><script id="__NEXT_DATA__">{next_data_json}</script></head><body></body></html>'
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _make_response(html)

    with patch("src.tools.web_scraper.httpx.Client", return_value=mock_client):
        emails = scrape_emails_from_url("https://linktr.ee/somehandle")

    assert "creator@mysite.com" in emails


def test_linktree_url_falls_back_to_html_scrape():
    # No __NEXT_DATA__, but email present in plain HTML
    html = "<html><body>Contact: fallback@mysite.com</body></html>"
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = _make_response(html)

    with patch("src.tools.web_scraper.httpx.Client", return_value=mock_client):
        emails = scrape_emails_from_url("https://linktr.ee/somehandle")

    assert "fallback@mysite.com" in emails


def test_linktree_returns_empty_on_failure():
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.side_effect = httpx.ConnectError("refused")

    with patch("src.tools.web_scraper.httpx.Client", return_value=mock_client):
        emails = scrape_emails_from_url("https://linktr.ee/somehandle")

    assert emails == []
