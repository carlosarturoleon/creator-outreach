"""
Anthropic Message Batches API client for bulk email generation.

Submits one email-generation request per influencer, polls until the batch
completes, and returns structured results as
{channel_id: {"subject_line": str, "email_body": str, "personalization_hooks": list}}.

Structured output is obtained via tool_use (no JSON parsing needed).

Behavior is configured via email_config.yaml at the repo root.
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
# Load email_config.yaml once at module import time
# ---------------------------------------------------------------------------
_CONFIG_PATH = Path(__file__).parent.parent.parent / "email_config.yaml"


def _load_email_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


_cfg = _load_email_config()

# Batch settings
_DEFAULT_POLL_INTERVAL: int = _cfg["batch"]["poll_interval"]
_DEFAULT_TIMEOUT: int = _cfg["batch"]["timeout"]
_DEFAULT_MAX_TOKENS: int = _cfg["batch"]["max_tokens"]

# Context truncation
_DESC_MAX_CHARS: int = _cfg["context"]["description_max_chars"]
_MAX_VIDEO_TITLES: int = _cfg["context"]["max_video_titles"]

# Tool schema constraints
_tool_cfg = _cfg["tool"]
_TOOL_NAME: str = _tool_cfg["name"]
_CONFIDENCE_MIN: int = _tool_cfg["confidence_min_to_pass"]

# Prompts
_SYSTEM_PROMPT: str = _cfg["system_prompt"].strip()
_USER_PROMPT_TEMPLATE: str = _cfg["user_prompt_template"].strip()

# Outreach config
_outreach = _cfg["outreach"]
    for ex in _offer["revenue_examples"]
]

# Email tool — built from config so constraints stay in one place
_EMAIL_TOOL = {
    "name": _TOOL_NAME,
    "description": _tool_cfg["description"],
    "input_schema": {
        "type": "object",
        "properties": {
            "subject_line": {
                "type": "string",
                "description": (
                    f"Personalized subject line. Max {_tool_cfg['subject_max_words']} words / "
                    f"{_tool_cfg['subject_max_chars']} characters. "
                    "Must pass the 'from a peer' test. No banned words."
                ),
            },
            "subject_strategy_used": {
                "type": "string",
                "enum": _tool_cfg["subject_strategies"],
                "description": "Which subject line strategy was applied.",
            },
            "email_body": {
                "type": "string",
                "description": (
                    "Full email body following the exact 4-section structure. "
                    f"{_tool_cfg['body_min_words']}–{_tool_cfg['body_max_words']} words. "
                    "Includes closing signature block."
                ),
            },
            "language": {
                "type": "string",
                "enum": _tool_cfg["languages"],
                "description": "'es' only for channels whose description AND video titles are primarily in Spanish. 'en' for everything else.",
            },
            "referenced_video_title": {
                "type": "string",
                "description": "The single most relevant video title cited in the hook sentence.",
            },
            "personalization_hooks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "All specific video titles or content pieces referenced anywhere in the email.",
            },
            "confidence_score": {
                "type": "integer",
                "description": (
                    f"Self-assessed quality score 1–10. Score against: "
                    f"(1) subject is under {_tool_cfg['subject_max_chars']} chars, "
                    "(2) hook names a specific video, "
                    "(3) pain point matches their actual niche, "
                    "(4) no banned phrases present, "
                    "(5) CTA is low-pressure. "
                    f"If score <{_CONFIDENCE_MIN}, regenerate with a different approach before returning."
                ),
            },
        },
        "required": [
            "subject_line",
            "subject_strategy_used",
            "email_body",
            "language",
            "referenced_video_title",
            "personalization_hooks",
            "confidence_score",
        ],
    },
}


def build_email_requests(
    influencers: list[dict],
    enriched_map: dict[str, dict],
    model: str | None = None,
    max_tokens: int | None = None,
) -> list[dict]:
    """
    Build a list of MessageBatchRequestParam dicts for email generation.

    Each entry uses tool_use to get structured {subject_line, email_body,
    personalization_hooks} output. custom_id = channel_id.

    Args:
        influencers: list of scored influencer dicts (must have llm_rationale, niche_tags)
        enriched_map: channel_id -> enriched channel dict (for recent_video_titles)
        model: Claude model ID (defaults to settings.claude_model)
        max_tokens: max tokens per response (defaults to email_config.yaml batch.max_tokens)

    Returns:
        list of request dicts ready for client.messages.batches.create(requests=...)
    """
    model = model or settings.claude_model
    max_tokens = max_tokens if max_tokens is not None else _DEFAULT_MAX_TOKENS
    requests = []
    for influencer in influencers:
        cid = influencer["channel_id"]
        ch = enriched_map.get(cid, {})

        niche_tags = influencer.get("niche_tags", [])
        llm_rationale = influencer.get(
            "llm_rationale",
            influencer.get("relevance_rationale", "strong fit for the affiliate program"),
        )

        description = ch.get("description", "") or ""
        user_msg = _USER_PROMPT_TEMPLATE.format(
            channel_title=influencer["channel_title"],
            subscribers=influencer.get("subscriber_count", 0),
            engagement_rate=influencer.get("engagement_rate", 0.0),
            description=description[:_DESC_MAX_CHARS],
            llm_rationale=llm_rationale,
            video_titles=", ".join(ch.get("recent_video_titles", [])[:_MAX_VIDEO_TITLES]),
            niche_tags=", ".join(niche_tags) if niche_tags else "marketing analytics",
            purpose=_outreach["purpose"],
            cta_en=_outreach["cta_en"],
            cta_es=_outreach["cta_es"],
            body_min_words=_tool_cfg["body_min_words"],
            body_max_words=_tool_cfg["body_max_words"],
            confidence_min_to_pass=_CONFIDENCE_MIN,
        )

        requests.append({
            "custom_id": cid,
            "params": {
                "model": model,
                "max_tokens": max_tokens,
                "system": _SYSTEM_PROMPT,
                "tools": [_EMAIL_TOOL],
                "tool_choice": {"type": "tool", "name": _TOOL_NAME},
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
                inp = tool_block.input
                results[cid] = {
                    "subject_line": inp.get("subject_line", ""),
                    "subject_strategy_used": inp.get("subject_strategy_used", ""),
                    "email_body": inp.get("email_body", ""),
                    "language": inp.get("language", "en"),
                    "referenced_video_title": inp.get("referenced_video_title", ""),
                    "personalization_hooks": inp.get("personalization_hooks", []),
                    "confidence_score": inp.get("confidence_score", 0),
                    "success": True,
                }
            else:
                log.error("fetch_email_results — no tool_use block for %s", cid)
                results[cid] = {
                    "subject_line": "",
                    "subject_strategy_used": "",
                    "email_body": "",
                    "language": "en",
                    "referenced_video_title": "",
                    "personalization_hooks": [],
                    "confidence_score": 0,
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
                "subject_strategy_used": "",
                "email_body": "",
                "language": "en",
                "referenced_video_title": "",
                "personalization_hooks": [],
                "confidence_score": 0,
                "success": False,
            }

    return results
