"""
Anthropic Message Batches API client for bulk email generation.

Submits one email-generation request per influencer, polls until the batch
completes, and returns structured results as
{channel_id: {"subject_line": str, "email_body": str, "personalization_hooks": list}}.

Structured output is obtained via tool_use (no JSON parsing needed).
"""
import time

import anthropic

from src.config import settings
from src.logger import get_logger

log = get_logger(__name__)

_DEFAULT_POLL_INTERVAL = 15
_DEFAULT_TIMEOUT = 3600

_EMAIL_TOOL = {
    "name": "write_email",
    "description": "Write a personalized affiliate outreach email for this YouTube creator.",
    "input_schema": {
        "type": "object",
        "properties": {
            "subject_line": {
                "type": "string",
                "description": "Personalized subject line referencing the creator's specific content or niche",
            },
            "email_body": {
                "type": "string",
                "description": "Full email body including the closing signature block",
            },
            "personalization_hooks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific video titles or content pieces from the channel referenced in the email",
            },
        },
        "required": ["subject_line", "email_body", "personalization_hooks"],
    },
}

_SYSTEM_PROMPT = """\
You are Carlos Leon, a marketing data expert at Windsor.ai, writing outreach emails to \
YouTube creators to invite them into Windsor.ai's affiliate program.

Windsor.ai facts:
- Connects 325+ ad platforms (Google Ads, Meta, TikTok, LinkedIn, Shopify, HubSpot, etc.) \
to any BI tool, spreadsheet, or data warehouse — no code needed
- Works with Looker Studio, Google Sheets, Claude, BigQuery, Snowflake, and more
- Live Claude connector: https://claude.com/connectors/windsor-ai
- Affiliate offer: 15% discount code for their audience + 30% lifetime revenue share per referral
- Revenue share examples to include: 10 clients at $100/mo = $300/mo passive | \
50 clients = $1,500/mo | 100 clients = $3,000/mo

Every email must be written entirely in the language detected from the channel's description \
and video titles. If the channel is primarily in Spanish, write the entire email in Spanish, \
including the closing lines.\
"""

_USER_PROMPT_TEMPLATE = """\
Write a personalized outreach email for this YouTube creator using the write_email tool.

Creator:
- Channel: {channel_title} ({subscribers:,} subscribers, {engagement_rate:.2f}% engagement)
- Channel description: {description}
- Why they fit Windsor.ai: {llm_rationale}
- Recent video titles: {video_titles}
- Niche tags: {niche_tags}

For the greeting, follow this priority order:
1. Extract the creator's first name from the channel description if mentioned \
(e.g. "My name is X", "I'm X", "Hi, I'm X", "Soy X", "Me llamo X"). Use that first name.
2. If the channel appears to be a company, agency, or brand — indicated by words like Agency, \
Media, Studio, Group, LLC, Inc., Corp, Co., Consulting, Marketing, Digital, Solutions, or the \
name reads as a brand rather than a person — use a neutral team greeting:
   - English: "Hi [Company Name] team,"
   - Spanish: "Hola, equipo de [Company Name],"
3. Otherwise, if the name reads like a personal handle or individual creator, use it directly:
   - English: "Hey [channel name],"
   - Spanish: "Hola [channel name],"

The email MUST follow this exact structure — use the same section headings and order:

---

Use the language-appropriate greeting:
- If writing in Spanish: "Hola [nombre],"
- If writing in English or any other language: "Hey [name],"

[1 sentences — Hook + credibility] Open by acknowledging what makes their content stand out \
and what their track record or audience focus reveals about the problems they solve. \
Reference something specific from the channel (a video title, their niche, their results).

[2–3 sentences — Intro + solution] "I'm Carlos Leon from Windsor.ai." Describe what Windsor.ai \
does for their specific audience — which platforms it connects, which destinations it syncs to \
(Looker Studio, Google Sheets, Claude, etc.), and what insight that unlocks. \
Include the Claude connector link: https://claude.com/connectors/windsor-ai

We are offering you:
• 15% discount code for their audience.
• 30% lifetime revenue share for every subscriber they refer.

That means:
• If they refer just 10 clients at $100/month = $300/month passive income
• If they refer 50 clients = $1,500/month
• If they refer 100 clients = $3,000/month

Why I think this is a strong fit:

[2–3 sentences — Pain + Windsor.ai value] Describe the specific data bottleneck their audience \
hits next (e.g. tracking ROAS across platforms, understanding which products are profitable, \
making budget decisions on real numbers). 

And with one video mention, the revenue can keep compounding because the rev share is lifetime.

Worth exploring?

Best,
Carlos Leon
Looker Studio & Marketing Data Expert
Windsor.ai

---

Tone: direct and genuine — one professional writing to another.
No flattery openers ("amazing channel", "love your work"). No "I hope this finds you well". \
No "sponsorship".

Language: detect the channel's language from the description and video titles. \
If the channel is primarily in Spanish, write the entire email in Spanish, including the \
subject line — use "30% de comisión vitalicia para tu audiencia". \
In all other cases — including Portuguese, French, or any other language — write in English \
with subject line "30% lifetime rev share for your audience".\
"""


def build_email_requests(
    influencers: list[dict],
    enriched_map: dict[str, dict],
    model: str | None = None,
    max_tokens: int = 1024,
) -> list[dict]:
    """
    Build a list of MessageBatchRequestParam dicts for email generation.

    Each entry uses tool_use to get structured {subject_line, email_body,
    personalization_hooks} output. custom_id = channel_id.

    Args:
        influencers: list of scored influencer dicts (must have llm_rationale, niche_tags)
        enriched_map: channel_id -> enriched channel dict (for recent_video_titles)
        model: Claude model ID (defaults to settings.claude_model)
        max_tokens: max tokens per response

    Returns:
        list of request dicts ready for client.messages.batches.create(requests=...)
    """
    model = model or settings.claude_model
    requests = []
    for influencer in influencers:
        cid = influencer["channel_id"]
        ch = enriched_map.get(cid, {})

        niche_tags = influencer.get("niche_tags", [])
        llm_rationale = influencer.get(
            "llm_rationale",
            influencer.get("relevance_rationale", "strong fit for Windsor.ai affiliate program"),
        )

        description = ch.get("description", "") or ""
        user_msg = _USER_PROMPT_TEMPLATE.format(
            channel_title=influencer["channel_title"],
            subscribers=influencer.get("subscriber_count", 0),
            engagement_rate=influencer.get("engagement_rate", 0.0),
            description=description[:600],
            llm_rationale=llm_rationale,
            video_titles=", ".join(ch.get("recent_video_titles", [])[:8]),
            niche_tags=", ".join(niche_tags) if niche_tags else "marketing analytics",
        )

        requests.append({
            "custom_id": cid,
            "params": {
                "model": model,
                "max_tokens": max_tokens,
                "system": _SYSTEM_PROMPT,
                "tools": [_EMAIL_TOOL],
                "tool_choice": {"type": "tool", "name": "write_email"},
                "messages": [{"role": "user", "content": user_msg}],
            },
        })
    return requests


def submit_batch(requests: list[dict]) -> str:
    """
    Submit a batch of email requests to the Anthropic Batches API.

    Returns:
        batch_id: str — used to poll and retrieve results
    """
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    batch = client.messages.batches.create(requests=requests)
    log.info("submit_batch — batch_id=%s, %d email requests submitted", batch.id, len(requests))
    return batch.id


def wait_for_batch(
    batch_id: str,
    poll_interval: int = _DEFAULT_POLL_INTERVAL,
    timeout: int = _DEFAULT_TIMEOUT,
) -> None:
    """
    Block until the batch reaches processing_status == 'ended' or timeout.

    Raises:
        TimeoutError: if the batch does not complete within timeout seconds
    """
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    elapsed = 0
    while elapsed < timeout:
        batch = client.messages.batches.retrieve(batch_id)
        counts = batch.request_counts
        log.info(
            "wait_for_batch — status=%s elapsed=%ds "
            "processing=%d succeeded=%d errored=%d expired=%d canceled=%d",
            batch.processing_status, elapsed,
            counts.processing, counts.succeeded,
            counts.errored, counts.expired, counts.canceled,
        )
        if batch.processing_status == "ended":
            return
        time.sleep(poll_interval)
        elapsed += poll_interval
    raise TimeoutError(
        f"Email batch {batch_id} did not complete within {timeout}s."
    )


def fetch_email_results(batch_id: str) -> dict[str, dict]:
    """
    Retrieve and parse results from a completed email batch.

    Returns:
        dict mapping channel_id -> {
            "subject_line": str,
            "email_body": str,
            "personalization_hooks": list[str],
            "success": bool,
        }
    """
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    results: dict[str, dict] = {}

    for item in client.messages.batches.results(batch_id):
        cid = item.custom_id

        if item.result.type == "succeeded":
            content = item.result.message.content
            tool_block = next(
                (b for b in content if getattr(b, "type", None) == "tool_use"),
                None,
            )
            if tool_block and hasattr(tool_block, "input"):
                results[cid] = {
                    "subject_line": tool_block.input.get("subject_line", ""),
                    "email_body": tool_block.input.get("email_body", ""),
                    "personalization_hooks": tool_block.input.get("personalization_hooks", []),
                    "success": True,
                }
            else:
                log.error("fetch_email_results — no tool_use block for %s", cid)
                results[cid] = {
                    "subject_line": "",
                    "email_body": "",
                    "personalization_hooks": [],
                    "success": False,
                }
        else:
            error_msg = getattr(
                getattr(item.result, "error", None), "message",
                str(item.result),
            )
            log.error("fetch_email_results — batch item %s failed: %s", cid, error_msg)
            results[cid] = {
                "subject_line": "",
                "email_body": "",
                "personalization_hooks": [],
                "success": False,
            }

    return results
