import re

from src.config import settings
from src.db.database import Database
from src.logger import get_logger
from src.state import GraphState
from src.tools.batch_email_client import (
    build_email_requests,
    fetch_email_results,
    submit_batch,
    wait_for_batch,
)

log = get_logger(__name__)

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def _extract_email(text: str) -> str | None:
    """Return the first email address found in text, or None."""
    m = _EMAIL_RE.search(text or "")
    return m.group(0) if m else None


def generate_emails(state: GraphState) -> dict:
    """
    Node 5: Generate personalized outreach emails via Anthropic Message Batches API.

    Submits all influencers in a single batch, polls until complete, persists
    results to SQLite. Uses llm_rationale (from the LLM scorer) and niche_tags
    for richer, more personalized email content.

    Sender persona: Carlos Leon, Looker Studio & Marketing Data Expert, Windsor.ai.
    """
    db = Database()
    errors: list[str] = []

    # Prefer scraped_channels (has contact_emails from web scraping), fall back to enriched_channels
    source_channels = state.get("scraped_channels") or state.get("enriched_channels", [])
    enriched_map: dict[str, dict] = {
        ch["channel_id"]: ch for ch in source_channels
    }
    influencers = state.get("scored_influencers", [])

    if not influencers:
        log.info("generate_emails — no influencers to email, skipping")
        return {"outreach_emails": [], "error_log": [], "current_phase": "email_generation_complete"}

    # Build contact email map: prefer contact_email already resolved (from enrich or scraping),
    # fall back to regex scan of description for backwards compatibility.
    contact_map = {
        inf["channel_id"]: (
            enriched_map.get(inf["channel_id"], {}).get("contact_email")
            or _extract_email(enriched_map.get(inf["channel_id"], {}).get("description", ""))
        )
        for inf in influencers
    }

    # Only generate emails for influencers with a known contact email
    influencers = [inf for inf in influencers if contact_map.get(inf["channel_id"])]
    contacts_found = len(influencers)

    if not influencers:
        log.info("generate_emails — no influencers have a contact email, skipping batch")
        return {"outreach_emails": [], "error_log": [], "current_phase": "email_generation_complete"}

    # Skip influencers who are already in the Windsor.ai affiliate program
    promoter_emails = db.get_promoter_emails()
    if promoter_emails:
        before = len(influencers)
        influencers = [
            inf for inf in influencers
            if contact_map.get(inf["channel_id"], "").lower() not in promoter_emails
        ]
        skipped_promoters = before - len(influencers)
        if skipped_promoters:
            log.info(
                "generate_emails — skipped %d influencer(s) already in affiliate program",
                skipped_promoters,
            )

    if not influencers:
        log.info("generate_emails — all remaining influencers are existing affiliates, skipping batch")
        return {"outreach_emails": [], "error_log": [], "current_phase": "email_generation_complete"}

    log.info("generate_emails START — %d influencer(s) have contact email, submitting batch", contacts_found)

    # Build and submit batch
    requests = build_email_requests(
        influencers=influencers,
        enriched_map=enriched_map,
        model=settings.claude_model,
    )

    try:
        batch_id = submit_batch(requests)
    except Exception as e:
        err = f"[generate_emails] Batch submission failed: {e}"
        log.error(err)
        return {
            "outreach_emails": [],
            "error_log": [err],
            "current_phase": "email_generation_complete",
        }

    # Poll until done
    try:
        wait_for_batch(batch_id)
    except TimeoutError as e:
        err = f"[generate_emails] {e}"
        log.error(err)
        errors.append(err)

    # Retrieve results
    try:
        results = fetch_email_results(batch_id)
    except Exception as e:
        err = f"[generate_emails] Result fetch failed: {e}"
        log.error(err)
        return {
            "outreach_emails": [],
            "error_log": errors + [err],
            "current_phase": "email_generation_complete",
        }

    # Assemble email_data dicts and persist
    emails: list[dict] = []
    for influencer in influencers:
        cid = influencer["channel_id"]
        result = results.get(cid, {})

        if result.get("success"):
            subject = result["subject_line"]
            body = result["email_body"].replace(" —", ",").replace("—", ",")
            hooks = result["personalization_hooks"]
        else:
            errors.append(f"[generate_emails] Batch failed for {cid}")
            subject = "Windsor.ai Affiliate Opportunity"
            body = "[Email generation failed - please retry]"
            hooks = []

        email_data = {
            "channel_id": cid,
            "channel_title": influencer["channel_title"],
            "subject_line": subject,
            "email_body": body,
            "personalization_hooks": hooks,
            "contact_email": contact_map.get(cid),
        }
        emails.append(email_data)

        log.info("  %s — subject: %s", influencer["channel_title"], subject[:60])

        try:
            db.upsert_email(email_data)
        except Exception as e:
            err_msg = f"[generate_emails] DB upsert failed for {cid}: {e}"
            log.error(err_msg)
            errors.append(err_msg)

    log.info(
        "generate_emails DONE — %d emails generated, %d with contact email, %d errors",
        len(emails), contacts_found, len(errors),
    )
    return {
        "outreach_emails": emails,
        "error_log": errors,
        "current_phase": "email_generation_complete",
    }
