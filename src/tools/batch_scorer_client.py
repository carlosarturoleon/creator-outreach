"""
Anthropic Message Batches API client for LLM-powered affiliate-fit scoring.

Submits one scoring request per channel, polls until the batch completes,
and returns structured results as {channel_id: {"llm_score": int, "llm_rationale": str}}.

Structured output is obtained via tool_use (most reliable — no JSON parsing needed).
"""
import time

import anthropic

from src.config import settings
from src.logger import get_logger

log = get_logger(__name__)

_DEFAULT_POLL_INTERVAL = 15     # seconds between status checks
_DEFAULT_TIMEOUT = 3600         # 1 hour max wait

_SCORE_TOOL = {
    "name": "score_channel",
    "description": "Return Windsor.ai affiliate fit score for this YouTube channel.",
    "input_schema": {
        "type": "object",
        "properties": {
            "llm_score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 10,
                "description": "Affiliate fit score from 0 (wrong niche) to 10 (perfect fit)",
            },
            "llm_rationale": {
                "type": "string",
                "description": "1-2 sentence evidence-based explanation citing specific channel signals",
            },
        },
        "required": ["llm_score", "llm_rationale"],
    },
}

_SYSTEM_PROMPT = """\
You are an affiliate partnership analyst for Windsor.ai.

Windsor.ai affiliate program:
- No-code marketing data connector: pulls data from 300+ ad platforms (Google Ads, Meta, TikTok, \
LinkedIn, Shopify) into any BI tool, spreadsheet, or data warehouse.
- Solves a real pain: marketers, analysts, and developers waste hours manually exporting \
and combining ad/analytics data.
- Affiliate program ONLY (no sponsorships, no paid placements). Creators mention the tool \
naturally in relevant videos.
- Commission: 30% RECURRING forever. Monthly payouts. Free to join.

Windsor.ai data sources (325+ total): Google Ads, Meta/Facebook Ads, TikTok Ads, LinkedIn Ads, \
Pinterest Ads, Snapchat Ads, Amazon Ads, Google Analytics 4 (GA4), Shopify, HubSpot, Salesforce, \
Stripe, Amazon Seller, and 300+ more.
Windsor.ai destinations: Looker Studio, Google Sheets, BigQuery, Snowflake, Amazon Redshift, \
Power BI, Tableau, Microsoft Excel, Amazon S3.

Windsor.ai serves multiple audience profiles — any of these is a strong fit:
1. Paid ads managers: Run Google Ads, Meta, TikTok, LinkedIn, Pinterest, Snapchat campaigns. \
Need attribution and cross-channel ROI reporting.
2. Marketing analysts / data analysts: Work with GA4, Looker Studio, BigQuery, Tableau, Power BI. \
Build marketing dashboards.
3. eCommerce operators: Run Shopify, WooCommerce, Amazon stores. Need to connect ad spend to \
revenue data.
4. CRM / RevOps users: Work with HubSpot, Salesforce. Need to unify CRM data with ad platform data.
5. Freelancers / agencies: Manage multiple client ad accounts. Need automated reporting workflows.
6. Data engineers: Build marketing data pipelines into Snowflake, Redshift, BigQuery.
7. Entrepreneurs / SaaS founders: Track marketing ROI, want automated dashboards without a data team.
8. BI / reporting educators: Teach Looker Studio, Power BI, Tableau, Google Sheets with real \
marketing data.

Score using the score_channel tool. Rubric:
  0-2  Wrong niche entirely (gaming, lifestyle, cooking, crypto, fitness, fashion). \
Audience has no use for Windsor.ai.
  3-4  Too general (generic business advice, broad "make money online"). \
Audience unlikely to pay for data integration tools.
  5-6  Relevant niche but content too high-level or strategic; doesn't cover specific tools \
or technical setups.
  7-8  Strong fit — covers marketing tools, paid ads setup, analytics, or data workflows. \
Audience uses or would pay for tools like Windsor.ai.
  9-10 Perfect fit — channel specifically teaches GA4, marketing attribution, cross-channel \
reporting, paid ads dashboards, Shopify analytics, or marketing data pipelines. \
Creator likely already recommends tools.

Cite specific evidence from the channel data in your rationale (max 2 sentences).\
"""

_USER_PROMPT_TEMPLATE = """\
Channel data:
- Name: {channel_title}
- Subscribers: {subscriber_count:,}
- Engagement rate: {engagement_rate:.2f}%
- Description: {description}
- Keywords: {keywords}
- Recent video titles: {video_titles}
- Keyword match signals: {matched_keywords} (deterministic score: {keyword_score_raw}/50 pts)

Score this channel's Windsor.ai affiliate fit using the score_channel tool.\
"""


def build_scorer_requests(
    influencers: list[dict],
    enriched_map: dict[str, dict],
    model: str | None = None,
    max_tokens: int = 512,
) -> list[dict]:
    """
    Build a list of MessageBatchRequestParam dicts for batch submission.

    Each entry uses tool_use to get structured {llm_score, llm_rationale} output.
    custom_id = channel_id (used to match results back to influencers).

    Args:
        influencers: list of scored influencer dicts (from score_influencers node)
        enriched_map: channel_id -> enriched channel dict (description, video titles, etc.)
        model: Claude model ID (defaults to settings.claude_model)
        max_tokens: max tokens per response (512 is ample for a score + 2-sentence rationale)

    Returns:
        list of request dicts ready for client.messages.batches.create(requests=...)
    """
    model = model or settings.claude_model
    requests = []
    for influencer in influencers:
        cid = influencer["channel_id"]
        ch = enriched_map.get(cid, {})
        kw_result = influencer.get("score_breakdown", {})
        matched_kws = influencer.get("niche_tags", [])

        user_msg = _USER_PROMPT_TEMPLATE.format(
            channel_title=influencer["channel_title"],
            subscriber_count=influencer.get("subscriber_count", 0),
            engagement_rate=influencer.get("engagement_rate", 0.0),
            description=(ch.get("description") or "")[:500],   # truncate very long descriptions
            keywords=", ".join(ch.get("keywords", [])[:10]),
            video_titles=", ".join(ch.get("recent_video_titles", [])[:8]),
            matched_keywords=", ".join(matched_kws) if matched_kws else "none",
            keyword_score_raw=influencer.get("score_breakdown", {}).get("relevance", 0),
        )

        requests.append({
            "custom_id": cid,
            "params": {
                "model": model,
                "max_tokens": max_tokens,
                "system": _SYSTEM_PROMPT,
                "tools": [_SCORE_TOOL],
                "tool_choice": {"type": "tool", "name": "score_channel"},
                "messages": [{"role": "user", "content": user_msg}],
            },
        })
    return requests


def submit_batch(requests: list[dict]) -> str:
    """
    Submit a batch of scoring requests to the Anthropic Batches API.

    Args:
        requests: output of build_scorer_requests()

    Returns:
        batch_id: str — used to poll and retrieve results
    """
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    batch = client.messages.batches.create(requests=requests)
    log.info("submit_batch — batch_id=%s, %d requests submitted", batch.id, len(requests))
    return batch.id


def wait_for_batch(
    batch_id: str,
    poll_interval: int = _DEFAULT_POLL_INTERVAL,
    timeout: int = _DEFAULT_TIMEOUT,
) -> None:
    """
    Block until the batch reaches processing_status == 'ended' or timeout.

    Args:
        batch_id: returned by submit_batch()
        poll_interval: seconds between status checks
        timeout: max seconds to wait before raising TimeoutError

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
        f"Batch {batch_id} did not complete within {timeout}s. "
        "You can retrieve it later with fetch_scorer_results()."
    )


def fetch_scorer_results(batch_id: str) -> dict[str, dict]:
    """
    Retrieve and parse results from a completed batch.

    Args:
        batch_id: returned by submit_batch()

    Returns:
        dict mapping channel_id -> {
            "llm_score": int (0–10),
            "llm_rationale": str,
            "success": bool,
        }
        On failure per item: llm_score=0, llm_rationale contains error info.
    """
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    results: dict[str, dict] = {}

    for item in client.messages.batches.results(batch_id):
        cid = item.custom_id

        if item.result.type == "succeeded":
            # tool_use response: content[0] is a ToolUseBlock with .input dict
            content = item.result.message.content
            tool_block = next(
                (b for b in content if getattr(b, "type", None) == "tool_use"),
                None,
            )
            if tool_block and hasattr(tool_block, "input"):
                results[cid] = {
                    "llm_score": int(tool_block.input.get("llm_score", 0)),
                    "llm_rationale": tool_block.input.get("llm_rationale", ""),
                    "success": True,
                }
            else:
                log.error("fetch_scorer_results — no tool_use block for %s", cid)
                results[cid] = {
                    "llm_score": 0,
                    "llm_rationale": "Batch response missing tool_use block.",
                    "success": False,
                }
        else:
            # item.result.type == "errored" | "expired" | "canceled"
            error_msg = getattr(
                getattr(item.result, "error", None), "message",
                str(item.result),
            )
            log.error("fetch_scorer_results — batch item %s failed: %s", cid, error_msg)
            results[cid] = {
                "llm_score": 0,
                "llm_rationale": f"Batch item failed: {error_msg}",
                "success": False,
            }

    return results
