"""
Web scraper utility for extracting contact emails from URLs found in channel descriptions.

Public API:
  extract_urls_from_text(text) -> list[str]
  scrape_emails_from_url(url, timeout=5) -> list[str]
"""

import json
import re
from urllib.parse import urljoin, urlparse

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


def _clean_html(html: str) -> str:
    """Remove input/textarea tags before email scanning to avoid placeholder pollution."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["input", "textarea"]):
        tag.decompose()
    return str(soup)


def _is_junk_email(email: str) -> bool:
    """Return True if the email is a known placeholder or system address."""
    email = email.lower()
    # Reject Sentry DSN-style paths embedded as email addresses
    if email.startswith("/"):
        return True
    # Reject JSON unicode escape artifacts (e.g. u003esupport@...)
    if re.search(r"u[0-9a-f]{4}", email.split("@")[0]):
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
        found = {e for e in scrape_emails(_clean_html(html)) if not _is_junk_email(e)}
        return sorted(found) if found else []
    except Exception as exc:
        log.debug("Linktree HTML email scrape failed for %s: %s", url, exc)
        return []


_CONTACT_KEYWORDS = {"contact", "about", "reach", "hire", "work-with", "get-in-touch", "connect", "touch"}


def _score_contact_link(url: str) -> int:
    lower = url.lower()
    return sum(1 for kw in _CONTACT_KEYWORDS if kw in lower)


def _find_contact_links(html: str, base_url: str) -> list[str]:
    """Extract and rank nav/footer links that look like contact/about pages."""
    soup = BeautifulSoup(html, "html.parser")
    base_netloc = urlparse(base_url).netloc

    # Prefer nav/footer — that's where contact links reliably live
    containers = soup.find_all(["nav", "footer"]) or [soup]

    seen: set[str] = set()
    candidates: list[tuple[int, str]] = []

    for container in containers:
        for tag in container.find_all("a", href=True):
            href = tag["href"].split("#")[0].split("?")[0].strip()
            if not href or href.startswith("mailto:"):
                continue
            full = urljoin(base_url, href) if not href.startswith("http") else href
            if urlparse(full).netloc != base_netloc:
                continue
            score = _score_contact_link(full)
            if score > 0 and full not in seen:
                seen.add(full)
                candidates.append((score, full))

    candidates.sort(reverse=True)
    return [u for _, u in candidates[:4]]


def _scrape_generic(url: str, timeout: int) -> list[str]:
    """Fetch a URL and extract contact emails.

    Strategy:
    1. Fetch the page with httpx.
    2. Find contact/about links in nav and footer.
    3. Add common fallback paths (/contact, /contact-us, /about).
    4. Scrape all candidates and return first match.
    5. If nothing found, retry candidates with playwright (JS-rendered fallback).
    """
    try:
        with httpx.Client(headers=_HEADERS, timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        log.debug("Generic fetch failed for %s: %s", url, exc)
        return []

    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    # If on a subdomain (e.g. blog.keitaro.io), also try the root domain
    netloc = parsed.netloc
    parts = netloc.split(".")
    root_base = None
    if len(parts) > 2:
        root_base = f"{parsed.scheme}://{'.'.join(parts[-2:])}"

    # Build ordered list: nav/footer candidates first, then fallbacks, then the page itself
    candidates: list[str] = _find_contact_links(html, url)
    for fallback in [f"{base}/contact", f"{base}/contact-us", f"{base}/about"]:
        if fallback not in candidates:
            candidates.append(fallback)
    if root_base:
        for fallback in [f"{root_base}/contact", f"{root_base}/contact-us", f"{root_base}/about"]:
            if fallback not in candidates:
                candidates.append(fallback)
    if url not in candidates:
        candidates.append(url)

    # --- httpx pass ---
    # Fetch all candidate pages first, then scrape outside the client context.
    # email_scraper has internal state that produces wrong results when called
    # inside an active httpx.Client session.
    pages: list[tuple[str, str]] = []
    try:
        with httpx.Client(headers=_HEADERS, timeout=timeout, follow_redirects=True) as client:
            for subpage in candidates:
                try:
                    page_html = html if subpage == url else client.get(subpage).text
                    pages.append((subpage, page_html))
                except Exception:
                    continue
    except Exception:
        pass

    for subpage, page_html in pages:
        found = {e for e in scrape_emails(_clean_html(page_html)) if not _is_junk_email(e)}
        if found:
            log.debug("Found emails on %s", subpage)
            return sorted(found)

    # --- playwright fallback (JS-rendered pages) ---
    log.debug("No emails via httpx for %s — retrying with playwright", url)
    return _scrape_with_playwright(candidates, timeout)


def _scrape_with_playwright(candidates: list[str], timeout: int) -> list[str]:
    """Re-fetch candidate pages using a headless browser and extract emails."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.debug("playwright not installed — skipping JS fallback")
        return []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})
            for subpage in candidates:
                try:
                    page.goto(subpage, timeout=timeout * 1000, wait_until="networkidle")
                    html = page.content()
                    found = {e.lower() for e in scrape_emails(_clean_html(html)) if not _is_junk_email(e)}
                    if found:
                        log.debug("playwright found emails on %s", subpage)
                        browser.close()
                        return sorted(found)
                except Exception:
                    continue
            browser.close()
    except Exception as exc:
        log.debug("playwright fallback failed: %s", exc)

    return []
