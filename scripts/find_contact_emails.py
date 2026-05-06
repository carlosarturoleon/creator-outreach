#!/usr/bin/env python3
"""
Find contact emails from website URLs.

Usage:
    python -m scripts.find_contact_emails https://example.com https://other.com
    python -m scripts.find_contact_emails --file urls.txt
"""
import argparse
import sys
from src.tools.web_scraper import scrape_emails_from_url


def main():
    parser = argparse.ArgumentParser(description="Find contact emails from website URLs")
    parser.add_argument("urls", nargs="*", help="URLs to scrape")
    parser.add_argument("--file", "-f", help="Text file with one URL per line")
    args = parser.parse_args()

    urls = list(args.urls)
    if args.file:
        with open(args.file) as f:
            urls += [line.strip() for line in f if line.strip() and not line.startswith("#")]

    if not urls:
        parser.print_help()
        sys.exit(1)

    for url in urls:
        emails = scrape_emails_from_url(url)
        if emails:
            print(f"{url}")
            for email in emails:
                print(f"  {email}")
        else:
            print(f"{url}  (no emails found)")


if __name__ == "__main__":
    main()
