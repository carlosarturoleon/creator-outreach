"""
Anthropic Message Batches API client for LLM-powered affiliate-fit scoring.

Submits one scoring request per channel, polls until the batch completes,
and returns structured results as {channel_id: {"llm_score": int, "llm_rationale": str}}.

Structured output is obtained via tool_use (most reliable — no JSON parsing needed).

Behavior is configured via scorer_config.yaml at the repo root.
Secrets and API keys remain in .env / src/config.py.
"""
import time
from pathlib import Path

import anthropic
import yaml

from src.config import settings
from src.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Load scorer_config.yaml once at module import time
# ---------------------------------------------------------------------------
_CONFIG_PATH = Path(__file__).parent.parent.parent / "scorer_config.yaml"

def _load_scorer_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)

_cfg = _load_scorer_config()

# Batch settings
_DEFAULT_POLL_INTERVAL: int = _cfg["batch"]["poll_interval"]
_DEFAULT_TIMEOUT: int = _cfg["batch"]["timeout"]
_DEFAULT_MAX_TOKENS: int = _cfg["batch"]["max_tokens"]

# Context truncation
_DESC_MAX_CHARS: int = _cfg["context"]["description_max_chars"]
_MAX_KEYWORDS: int = _cfg["context"]["max_keywords"]
_MAX_VIDEO_TITLES: int = _cfg["context"]["max_video_titles"]

# Prompts
_SYSTEM_PROMPT: str = _cfg["system_prompt"].strip()
_USER_PROMPT_TEMPLATE: str = _cfg["user_prompt_template"].strip()

# Tool schema — built from config so name/description/range stay in one place
_tool_cfg = _cfg["tool"]
_SCORE_TOOL = {
    "name": _tool_cfg["name"],
    "description": _tool_cfg["description"],
    "input_schema": {
        "type": "object",
        "properties": {
            "llm_score": {
                "type": "integer",
                "minimum": _tool_cfg["score_min"],
                "maximum": _tool_cfg["score_max"],
                "description": (
                    f"Affiliate fit score from {_tool_cfg['score_min']} (wrong niche) "
                    f"to {_tool_cfg['score_max']} (perfect fit)"
                ),
            },
            "llm_rationale": {
                "type": "string",
                "description": "1-2 sentence evidence-based explanation citing specific channel signals",
            },
        },
        "required": ["llm_score", "llm_rationale"],
    },
}


def build_scorer_requests(
    influencers: list[dict],
    enriched_map: dict[str, dict],
    model: str | None = None,
    max_tokens: int | None = None,
) -> list[dict]:
    """
    Build a list of MessageBatchRequestParam dicts for batch submission.

    Each entry uses tool_use to get structured {llm_score, llm_rationale} output.
    custom_id = channel_id (used to match results back to influencers).

    Args:
        influencers: list of scored influencer dicts (from score_influencers node)
        enriched_map: channel_id -> enriched channel dict (description, video titles, etc.)
        model: Claude model ID (defaults to settings.claude_model)
        max_tokens: max tokens per response (defaults to scorer_config.yaml batch.max_tokens)

    Returns:
        list of request dicts ready for client.messages.batches.create(requests=...)
    """
    model = model or settings.claude_model
    max_tokens = max_tokens if max_tokens is not None else _DEFAULT_MAX_TOKENS
    requests = []
    for influencer in influencers:
        cid = influencer["channel_id"]
        ch = enriched_map.get(cid, {})
        matched_kws = influencer.get("niche_tags", [])

        user_msg = _USER_PROMPT_TEMPLATE.format(
            channel_title=influencer["channel_title"],
            subscriber_count=influencer.get("subscriber_count", 0),
            engagement_rate=influencer.get("engagement_rate", 0.0),
            description=(ch.get("description") or "")[:_DESC_MAX_CHARS],
            keywords=", ".join(ch.get("keywords", [])[:_MAX_KEYWORDS]),
            video_titles=", ".join(ch.get("recent_video_titles", [])[:_MAX_VIDEO_TITLES]),
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
                "tool_choice": {"type": "tool", "name": _tool_cfg["name"]},
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
