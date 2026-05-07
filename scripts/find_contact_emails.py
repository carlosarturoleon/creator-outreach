#!/usr/bin/env python3
"""
Find contact emails from website URLs.

Usage:
    python -m scripts.find_contact_emails https://example.com https://other.com
    python -m scripts.find_contact_emails --file urls.txt
    python -m scripts.find_contact_emails --verbose https://example.com
"""
import argparse
import logging
import sys
from src.tools.web_scraper import scrape_emails_from_url, _find_contact_links
import httpx


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


def scrape_verbose(url: str) -> list[str]:
    print(f"  Fetching {url} ...")
    try:
        resp = httpx.get(url, headers=_HEADERS, follow_redirects=True, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ERROR fetching homepage: {e}")
        return []

    from urllib.parse import urlparse
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    parts = parsed.netloc.split(".")
    root_base = f"{parsed.scheme}://{'.'.join(parts[-2:])}" if len(parts) > 2 else None

    candidates = _find_contact_links(resp.text, url)
    print(f"  Nav/footer contact links found: {candidates or '(none)'}")

    for fallback in [f"{base}/contact", f"{base}/contact-us", f"{base}/about"]:
        if fallback not in candidates:
            candidates.append(fallback)
    if root_base:
        for fallback in [f"{root_base}/contact", f"{root_base}/contact-us", f"{root_base}/about"]:
            if fallback not in candidates:
                candidates.append(fallback)
    if url not in candidates:
        candidates.append(url)

    from email_scraper import scrape_emails
    from src.tools.web_scraper import _is_junk_email, _scrape_with_playwright

    for subpage in candidates:
        print(f"  Trying {subpage} ...")
        try:
            page_html = resp.text if subpage == url else httpx.get(subpage, headers=_HEADERS, follow_redirects=True, timeout=10).text
            found = {e for e in scrape_emails(page_html) if not _is_junk_email(e)}
            if found:
                print(f"  Found emails on {subpage}")
                return sorted(found)
            else:
                print(f"  No emails on {subpage}")
        except Exception as e:
            print(f"  ERROR on {subpage}: {e}")

    print(f"  No emails via httpx — retrying with playwright (headless browser) ...")
    found = _scrape_with_playwright(candidates, timeout=10)
    if found:
        print(f"  Playwright found emails")
    else:
        print(f"  Playwright found nothing")
    return found


def main():
    parser = argparse.ArgumentParser(description="Find contact emails from website URLs")
    parser.add_argument("urls", nargs="*", help="URLs to scrape")
    parser.add_argument("--file", "-f", help="Text file with one URL per line")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show scraping steps")
    args = parser.parse_args()

    def normalize(u: str) -> str:
        if not u.startswith("http://") and not u.startswith("https://"):
            u = "https://" + u
        return u

    urls = [normalize(u) for u in args.urls]
    if args.file:
        with open(args.file) as f:
            urls += [normalize(line.strip()) for line in f if line.strip() and not line.startswith("#")]

    if not urls:
        parser.print_help()
        sys.exit(1)

    for url in urls:
        if args.verbose:
            print(f"\n{url}")
            emails = scrape_verbose(url)
        else:
            emails = scrape_emails_from_url(url)

        if emails:
            if not args.verbose:
                print(f"{url}")
            for email in emails:
                print(f"  {email}")
        else:
            if not args.verbose:
                print(f"{url}  (no emails found)")
            else:
                print(f"  (no emails found)")


if __name__ == "__main__":
    main()
