"""
regenerate_emails.py — Regenerate emails for LLM-scored influencers using
the new batch email client and prompt, without re-running the full pipeline.

Usage:
    python regenerate_emails.py
"""
import json
import sys

from src.db.database import Database
from src.config import settings
from src.logger import get_logger
from src.tools.batch_email_client import (
    build_email_requests,
    submit_batch,
    wait_for_batch,
    fetch_email_results,
)

log = get_logger(__name__)


def main() -> None:
    db = Database()

    # Load scored influencers with llm_score from DB
    with db._connect() as conn:
        score_rows = conn.execute("""
            SELECT s.channel_id, s.composite_score, s.llm_score, s.llm_rationale,
                   s.relevance_rationale, s.niche_tags,
                   c.channel_title, c.subscriber_count, c.engagement_rate,
                   c.description, c.keywords, c.recent_video_titles
            FROM scored_influencers s
            JOIN channels c ON s.channel_id = c.channel_id
            WHERE s.llm_score IS NOT NULL
            ORDER BY s.llm_score DESC, s.composite_score DESC
        """).fetchall()

    if not score_rows:
        print("No LLM-scored influencers found in DB. Run the pipeline first.")
        sys.exit(1)

    print(f"Found {len(score_rows)} LLM-scored influencer(s) — regenerating emails...\n")

    # Build influencer dicts and enriched_map matching what generate_emails expects
    influencers = []
    enriched_map = {}
    for row in score_rows:
        d = dict(row)
        cid = d["channel_id"]

        # Parse JSON fields
        try:
            niche_tags = json.loads(d["niche_tags"]) if d["niche_tags"] else []
        except (ValueError, TypeError):
            niche_tags = []
        try:
            keywords = json.loads(d["keywords"]) if d["keywords"] else []
        except (ValueError, TypeError):
            keywords = []
        try:
            recent_video_titles = json.loads(d["recent_video_titles"]) if d["recent_video_titles"] else []
        except (ValueError, TypeError):
            recent_video_titles = []

        influencers.append({
            "channel_id": cid,
            "channel_title": d["channel_title"],
            "subscriber_count": d["subscriber_count"],
            "engagement_rate": d["engagement_rate"],
            "composite_score": d["composite_score"],
            "llm_score": d["llm_score"],
            "llm_rationale": d["llm_rationale"],
            "relevance_rationale": d["relevance_rationale"],
            "niche_tags": niche_tags,
        })
        enriched_map[cid] = {
            "channel_id": cid,
            "description": d["description"] or "",
            "keywords": keywords,
            "recent_video_titles": recent_video_titles,
        }

    # Build and submit batch
    requests = build_email_requests(
        influencers=influencers,
        enriched_map=enriched_map,
        model=settings.claude_model,
    )

    print(f"Submitting batch of {len(requests)} email request(s)...")
    batch_id = submit_batch(requests)
    wait_for_batch(batch_id)
    results = fetch_email_results(batch_id)

    # Persist to DB
    import re
    email_re = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

    for influencer in influencers:
        cid = influencer["channel_id"]
        result = results.get(cid, {})
        ch = enriched_map[cid]

        contact_email_match = email_re.search(ch.get("description", ""))
        contact_email = contact_email_match.group(0) if contact_email_match else None

        if result.get("success"):
            subject = result["subject_line"]
            body = result["email_body"].replace(" —", ",").replace("—", ",")
            hooks = result["personalization_hooks"]
        else:
            subject = "Windsor.ai Affiliate Opportunity"
            body = "[Email generation failed - please retry]"
            hooks = []

        email_data = {
            "channel_id": cid,
            "channel_title": influencer["channel_title"],
            "subject_line": subject,
            "email_body": body,
            "personalization_hooks": hooks,
            "contact_email": contact_email,
        }

        db.upsert_email(email_data)
        print(f"  ✓ {influencer['channel_title']}")
        print(f"    Subject: {subject}")
        print(f"    Preview: {body[:120].strip()}...")
        print()

    print(f"Done — {len(influencers)} email(s) saved to DB.")
    print("Run: python send_emails.py --dry-run   to preview")
    print("Run: python send_emails.py --limit 3   to send")


if __name__ == "__main__":
    main()
