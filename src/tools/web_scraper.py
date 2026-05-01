"""
Web scraper utility for extracting contact emails from URLs found in channel descriptions.

Public API:
  extract_urls_from_text(text) -> list[str]
  scrape_emails_from_url(url, timeout=5) -> list[str]
"""

import json
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from email_scraper import scrape_emails

from src.logger import get_logger

log = get_logger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>\"']+")

_SKIP_DOMAINS = {
    "youtube.com",
    "youtu.be",
    "twitter.com",
    "x.com",
    "instagram.com",
    "facebook.com",
    "tiktok.com",
    "linkedin.com",
    "t.me",
    "discord.gg",
    "discord.com",
    "spotify.com",
    "apple.com",
    "patreon.com",
    "amzn.to",
    "amazon.com",
    "bit.ly",
    "goo.gl",
}

_LINKTREE_DOMAINS = {"linktr.ee", "linktree.com"}

# Emails that are clearly not real contact addresses
_JUNK_EMAIL_DOMAINS = {
    "sentry.io", "sentry.wixpress.com", "example.com", "cal.com",
    "agency.com", "apple.com", "company.com",
}
_JUNK_EMAIL_PREFIXES = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon",
    "postmaster", "webmaster", "abuse", "spam",
}


def _is_junk_email(email: str) -> bool:
    """Return True if the email is a known placeholder or system address."""
    email = email.lower()
    # Reject Sentry DSN-style paths embedded as email addresses
    if email.startswith("/"):
        return True
    try:
        local, domain = email.rsplit("@", 1)
    except ValueError:
        return True
    if domain in _JUNK_EMAIL_DOMAINS:
        return True
    if local in _JUNK_EMAIL_PREFIXES:
        return True
    # Reject hex-only local parts (Sentry DSN keys look like 32-char hex strings)
    if len(local) == 32 and all(c in "0123456789abcdef" for c in local):
        return True
    return False

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def extract_urls_from_text(text: str) -> list[str]:
    """Return up to 3 http/https URLs found in text.

    Strips trailing punctuation and filters out social media domains
    that never contain contact emails.
    """
    if not text:
        return []

    urls = []
    for raw in _URL_RE.findall(text):
        # Strip common trailing punctuation from natural prose
        url = raw.rstrip(")],.'\"")
        try:
            netloc = urlparse(url).netloc.lstrip("www.")
        except Exception:
            continue
        if netloc in _SKIP_DOMAINS:
            continue
        if url not in urls:
            urls.append(url)
        if len(urls) >= 3:
            break
    return urls


def scrape_emails_from_url(url: str, timeout: int = 5) -> list[str]:
    """Fetch a URL and return all email addresses found on the page.

    Routes Linktree URLs to a special handler that parses __NEXT_DATA__.
    Returns an empty list on any network or parsing error (never raises).
    """
    try:
        netloc = urlparse(url).netloc.lstrip("www.")
        if netloc in _LINKTREE_DOMAINS:
            handle = urlparse(url).path.strip("/")
            return _scrape_linktree(handle, timeout)
        return _scrape_generic(url, timeout)
    except Exception as exc:
        log.debug("scrape_emails_from_url error for %s: %s", url, exc)
        return []


def _scrape_linktree(handle: str, timeout: int) -> list[str]:
    """Fetch a Linktree profile and extract emails.

    Tries to parse the __NEXT_DATA__ JSON blob embedded by Next.js first,
    then falls back to running email-scraper on the raw HTML.
    """
    url = f"https://linktr.ee/{handle}"
    try:
        with httpx.Client(headers=_HEADERS, timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        log.debug("Linktree fetch failed for %s: %s", url, exc)
        return []

    # Try __NEXT_DATA__ JSON blob first (embedded by Next.js)
    try:
        soup = BeautifulSoup(html, "html.parser")
        script_tag = soup.find("script", {"id": "__NEXT_DATA__"})
        if script_tag and script_tag.string:
            # Run email-scraper on the JSON string as if it were plain text
            found = {e for e in scrape_emails(script_tag.string) if not _is_junk_email(e)}
            if found:
                return sorted(found)
    except Exception as exc:
        log.debug("Linktree __NEXT_DATA__ parse failed for %s: %s", url, exc)

    # Fall back to email-scraper on full HTML
    try:
        found = {e for e in scrape_emails(html) if not _is_junk_email(e)}
        return sorted(found) if found else []
    except Exception as exc:
        log.debug("Linktree HTML email scrape failed for %s: %s", url, exc)
        return []


def _scrape_generic(url: str, timeout: int) -> list[str]:
    """Fetch a generic URL and extract all email addresses using email-scraper."""
    try:
        with httpx.Client(headers=_HEADERS, timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        log.debug("Generic fetch failed for %s: %s", url, exc)
        return []

    try:
        found = {e for e in scrape_emails(html) if not _is_junk_email(e)}
        return sorted(found) if found else []
    except Exception as exc:
        log.debug("Generic email scrape failed for %s: %s", url, exc)
        return []
