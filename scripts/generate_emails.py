"""
generate_emails.py — Generate outreach emails for selected channels without running the full pipeline.

Reads selected influencers from the DB, calls the Anthropic Batches API,
and saves results back to outreach_emails.

Usage:
    python scripts/generate_emails.py                  # all channels with a contact email, not yet sent
    python scripts/generate_emails.py UCxxx UCyyy      # specific channel IDs (must have contact email)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import anthropic

from src.config import settings
from src.db.database import Database
from src.tools.batch_email_client import (
    build_email_requests,
    fetch_email_results,
    submit_batch,
    wait_for_batch,
)


def load_influencers(db: Database, channel_ids: list[str] | None) -> tuple[list[dict], dict[str, dict]]:
    """Return (influencers, enriched_map) for channels with a contact email not yet sent."""
    with db._connect() as conn:
        if channel_ids:
            placeholders = ",".join("?" * len(channel_ids))
            rows = conn.execute(
                f"""SELECT si.*, c.channel_title, c.description, c.subscriber_count,
                           c.engagement_rate, c.recent_video_titles, c.contact_email
                    FROM scored_influencers si
                    JOIN channels c USING (channel_id)
                    WHERE si.channel_id IN ({placeholders})
                      AND c.contact_email IS NOT NULL AND length(c.contact_email) > 0
                      AND si.channel_id NOT IN (SELECT channel_id FROM outreach_emails WHERE generated_at IS NOT NULL)
                      AND lower(c.contact_email) NOT IN (SELECT email FROM affiliate_promoters)""",
                channel_ids,
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT si.*, c.channel_title, c.description, c.subscriber_count,
                          c.engagement_rate, c.recent_video_titles, c.contact_email
                   FROM scored_influencers si
                   JOIN channels c USING (channel_id)
                   WHERE c.contact_email IS NOT NULL AND length(c.contact_email) > 0
                     AND si.channel_id NOT IN (SELECT channel_id FROM outreach_emails WHERE generated_at IS NOT NULL)
                     AND lower(c.contact_email) NOT IN (SELECT email FROM affiliate_promoters)"""
            ).fetchall()

    influencers = []
    enriched_map = {}
    for row in rows:
        d = dict(row)
        try:
            d["niche_tags"] = json.loads(d["niche_tags"]) if d["niche_tags"] else []
        except (ValueError, TypeError):
            d["niche_tags"] = []
        try:
            recent = json.loads(d["recent_video_titles"]) if d["recent_video_titles"] else []
        except (ValueError, TypeError):
            recent = []

        influencers.append(d)
        enriched_map[d["channel_id"]] = {
            "description": d.get("description", ""),
            "recent_video_titles": recent,
        }

    return influencers, enriched_map


def main() -> None:
    args = sys.argv[1:]
    channel_ids = [a for a in args if not a.startswith("--")] or None

    db = Database()
    influencers, enriched_map = load_influencers(db, channel_ids)

    if not influencers:
        print("No channels to generate emails for (no contact email, or all already sent).")
        sys.exit(0)

    has_email = influencers
    print(f"Generating emails for {len(has_email)} channel(s)...")

    requests = build_email_requests(
        influencers=has_email,
        enriched_map=enriched_map,
        model=settings.claude_model,
    )

    try:
        batch_id = submit_batch(requests)
    except anthropic.AuthenticationError:
        print("ERROR: Invalid Anthropic API key. Check ANTHROPIC_API_KEY in your .env file.")
        sys.exit(1)
    except anthropic.PermissionDeniedError as e:
        if "credit balance" in str(e).lower():
            print("ERROR: Your Anthropic credit balance is too low.")
            print("Add credits at https://console.anthropic.com/settings/billing")
        else:
            print(f"ERROR: Anthropic permission denied: {e}")
        sys.exit(1)
    except anthropic.BadRequestError as e:
        if "credit balance" in str(e).lower():
            print("ERROR: Your Anthropic credit balance is too low.")
            print("Add credits at https://console.anthropic.com/settings/billing")
        else:
            print(f"ERROR: Bad request to Anthropic API: {e}")
        sys.exit(1)
    print(f"Batch submitted: {batch_id}")
    print("Waiting for completion (this usually takes 1-5 minutes)...")

    wait_for_batch(batch_id)
    results = fetch_email_results(batch_id)

    generated = 0
    for inf in has_email:
        cid = inf["channel_id"]
        result = results.get(cid, {})

        if result.get("success"):
            subject = result["subject_line"]
            body = result["email_body"].replace(" —", ",").replace("—", ",")
            hooks = result["personalization_hooks"]
            strategy = result.get("subject_strategy_used", "")
            score = result.get("confidence_score", "?")
            lang = result.get("language", "en")
            print(f"  ✓ {inf['channel_title']} [{lang}|{strategy}|score={score}]: {subject[:60]}")
        else:
            subject = "Affiliate Opportunity"
            body = "[Email generation failed - please retry]"
            hooks = []
            print(f"  ✗ {inf['channel_title']}: generation failed")

        db.upsert_email({
            "channel_id": cid,
            "channel_title": inf["channel_title"],
            "subject_line": subject,
            "email_body": body,
            "personalization_hooks": hooks,
            "contact_email": inf["contact_email"],
        })
        generated += 1

    print(f"\nDone — {generated} email(s) saved to DB.")
    print("Preview: python send_emails.py --dry-run")


if __name__ == "__main__":
    main()
