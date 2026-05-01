from src.db.database import Database
from src.logger import get_logger
from src.state import GraphState
from src.tools.web_scraper import extract_urls_from_text, scrape_emails_from_url

log = get_logger(__name__)

_MAX_URLS_PER_CHANNEL = 3


def scrape_contact_emails(state: GraphState) -> dict:
    """
    Node 5.5: Scrape contact emails from websites linked in channel descriptions.

    Runs after llm_score_influencers so we only scrape channels that passed all
    filters and scoring. Looks up descriptions from enriched_channels by channel_id,
    then scrapes URLs found in the description.

    All found emails are stored in contact_emails (JSON list). The primary
    contact_email is set to the first found email if none was already set.

    Channels marked no_email=1 in the DB are skipped entirely.
    Writes results to scraped_channels (plain-overwrite state key).
    """
    scored = state.get("scored_influencers", [])
    if not scored:
        return {"scraped_channels": [], "current_phase": "scrape_complete"}

    enriched_map = {ch["channel_id"]: ch for ch in state.get("enriched_channels", [])}
    channels = [enriched_map[inf["channel_id"]] for inf in scored if inf["channel_id"] in enriched_map]

    if not channels:
        return {"scraped_channels": [], "current_phase": "scrape_complete"}

    db = Database()
    no_email_ids = db.get_no_email_channel_ids()

    updated = []
    scraped_count = 0

    for ch in channels:
        cid = ch.get("channel_id", "")

        if cid in no_email_ids:
            updated.append(ch)
            continue

        description = ch.get("description", "") or ""
        urls = extract_urls_from_text(description)
        if not urls:
            updated.append(ch)
            continue

        all_emails: list[str] = []
        for url in urls[:_MAX_URLS_PER_CHANNEL]:
            found = scrape_emails_from_url(url)
            for email in found:
                if email not in all_emails:
                    all_emails.append(email)

        if all_emails:
            primary = ch.get("contact_email") or all_emails[0]
            ch = {**ch, "contact_email": primary, "contact_emails": all_emails}
            scraped_count += 1
            log.info(
                "  %s — scraped %d email(s): %s",
                ch.get("channel_title", cid),
                len(all_emails),
                ", ".join(all_emails),
            )
            try:
                db.upsert_channel(ch)
            except Exception as exc:
                log.warning("  DB upsert failed for %s: %s", cid, exc)

        updated.append(ch)

    skipped_count = sum(1 for ch in channels if ch.get("channel_id") in no_email_ids)
    already_had = sum(1 for ch in channels if ch.get("contact_email") and ch["channel_id"] not in no_email_ids)
    no_email_found = len(updated) - scraped_count - already_had - skipped_count
    total_with_email = already_had + scraped_count
    log.info(
        "scrape_contact_emails — %d/%d have a contact email (%d already had, %d newly scraped), "
        "%d no email found, %d skipped (no_email flag)",
        total_with_email,
        len(channels),
        already_had,
        scraped_count,
        no_email_found,
        skipped_count,
    )
    return {"scraped_channels": updated, "current_phase": "scrape_complete"}
