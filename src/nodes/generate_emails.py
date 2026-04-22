import re
import time

from src.logger import get_logger
from src.state import GraphState
from src.db.database import Database
from src.tools.llm_client import get_llm
from src.models.outreach_email import EmailResult

log = get_logger(__name__)

_LLM_MAX_RETRIES = 4
_LLM_INITIAL_DELAY = 2.0


def _invoke_with_backoff(structured_llm, prompt: str):
    """Invoke LLM with exponential backoff on rate limit (429) errors."""
    delay = _LLM_INITIAL_DELAY
    last_exc = None
    for attempt in range(_LLM_MAX_RETRIES):
        try:
            return structured_llm.invoke(prompt)
        except Exception as e:
            last_exc = e
            err_str = str(e).lower()
            if "429" in err_str or "rate limit" in err_str or "overloaded" in err_str:
                if attempt < _LLM_MAX_RETRIES - 1:
                    log.warning("generate_emails — rate limited, retrying in %.1fs (attempt %d/%d)",
                                delay, attempt + 1, _LLM_MAX_RETRIES)
                    time.sleep(delay)
                    delay = min(delay * 2, 60.0)
                    continue
            raise
    raise last_exc

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def _extract_email(text: str) -> str | None:
    """Return the first email address found in text, or None."""
    m = _EMAIL_RE.search(text or "")
    return m.group(0) if m else None


EMAIL_PROMPT = """You are writing a personalized outreach email on behalf of Windsor.ai to invite a YouTube creator to their affiliate program.

Windsor.ai facts:
- No-code marketing data integration and attribution platform
- Connects 300+ data sources (Google Ads, Meta, TikTok, LinkedIn, Shopify, etc.) to BI tools
- Trusted by 10,000+ digital marketers and agencies
- Affiliate program: 30% RECURRING commissions forever, monthly payouts
- Sign-up is free; ideal for content creators who teach digital marketing, analytics, or SaaS tools

Creator details:
- Channel: {channel_title} ({subscribers:,} subscribers)
- Recent videos: {video_titles}
- Channel keywords: {keywords}
- Engagement rate: {engagement_rate:.2f}%
- Why this channel fits Windsor.ai: {rationale}

Email instructions:
1. Open with a specific, genuine compliment referencing one of their actual video titles or topics
2. Briefly explain Windsor.ai (2 sentences max) and why it fits their audience
3. Make the affiliate offer concrete: 30% recurring commissions, monthly payouts
4. Include a soft CTA: "I'd love to send you full details and a custom affiliate link"
5. Keep it under 200 words. Natural tone, not salesy.
6. Subject line should be personal and specific, not generic.

Respond with a JSON object matching the EmailResult schema."""


def generate_emails(state: GraphState) -> dict:
    """
    Node 5: Generate personalized outreach emails for each scored influencer.
    Uses Claude with structured output. Persists emails to SQLite.
    """
    llm = get_llm(temperature=0.7)
    structured_llm = llm.with_structured_output(EmailResult, method="json_schema")

    db = Database()
    errors: list[str] = []
    # Build lookup map from enriched channels for video titles / keywords
    enriched_map: dict[str, dict] = {
        ch["channel_id"]: ch for ch in state.get("enriched_channels", [])
    }

    influencers = state.get("scored_influencers", [])
    log.info("generate_emails START — generating emails for %d influencers", len(influencers))
    emails: list[dict] = []
    contacts_found = 0
    for i, influencer in enumerate(influencers, 1):
        cid = influencer["channel_id"]
        ch = enriched_map.get(cid, {})
        contact_email = _extract_email(ch.get("description", ""))
        if contact_email:
            contacts_found += 1

        prompt = EMAIL_PROMPT.format(
            channel_title=influencer["channel_title"],
            subscribers=influencer["subscriber_count"],
            video_titles=", ".join(ch.get("recent_video_titles", [])[:5]),
            keywords=", ".join(ch.get("keywords", [])[:8]),
            engagement_rate=influencer["engagement_rate"],
            rationale=influencer.get("relevance_rationale", ""),
        )

        log.info("  [%d/%d] Generating email for: %s", i, len(influencers), influencer["channel_title"])
        try:
            result: EmailResult = _invoke_with_backoff(structured_llm, prompt)
            subject = result.subject_line
            body = result.email_body
            hooks = result.personalization_hooks
        except Exception as e:
            err_msg = f"[generate_emails] LLM failed for {cid}: {e}"
            log.error("  LLM failed for %s: %s", cid, e)
            errors.append(err_msg)
            subject = "Windsor.ai Affiliate Opportunity"
            body = "[Email generation failed — please retry]"
            hooks = []

        email_data = {
            "channel_id": cid,
            "channel_title": influencer["channel_title"],
            "subject_line": subject,
            "email_body": body,
            "personalization_hooks": hooks,
            "contact_email": contact_email,
        }
        emails.append(email_data)

        try:
            db.upsert_email(email_data)
        except Exception as e:
            err_msg = f"[generate_emails] DB upsert failed for {cid}: {e}"
            log.error("  DB upsert failed for %s: %s", cid, e)
            errors.append(err_msg)

    log.info("generate_emails DONE — %d emails generated, %d with contact email, %d errors",
             len(emails), contacts_found, len(errors))
    return {
        "outreach_emails": emails,
        "error_log": errors,
        "current_phase": "email_generation_complete",
    }
