import math
import time

from src.state import GraphState
from src.db.database import Database
from src.tools.llm_client import get_llm
from src.models.scored_influencer import ScoringResult

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
                    print(f"[score_influencers] Rate limited, retrying in {delay:.1f}s (attempt {attempt + 1}/{_LLM_MAX_RETRIES})")
                    time.sleep(delay)
                    delay = min(delay * 2, 60.0)
                    continue
            raise
    raise last_exc

WINDSOR_RELEVANCE_PROMPT = """You are evaluating YouTube channels for the Windsor.ai affiliate program.

Windsor.ai is a no-code marketing data integration and attribution platform. It connects ad data from 300+ sources (Google Ads, Meta, TikTok, LinkedIn, Shopify, etc.) to BI tools (Google Sheets, Looker Studio, BigQuery, Tableau). Their audience is digital marketers, marketing analysts, CMOs, performance marketers, and data-driven teams.

The affiliate program pays 30% recurring commissions indefinitely.

Channel to evaluate:
- Title: {title}
- Description: {description}
- Channel keywords: {keywords}
- Recent video titles: {video_titles}
- Subscribers: {subscribers:,}
- Engagement rate: {engagement_rate:.2f}%

Score the relevance of this channel for Windsor.ai affiliate promotion (0-30 points).
Consider: Does their audience care about marketing analytics, attribution, ad data, SaaS tools, or performance marketing? Would Windsor.ai solve a real problem for their viewers?

Respond with a JSON object matching the ScoringResult schema."""


def _engagement_score(rate: float) -> float:
    """0-40 pts using log scale. At 1% ER → ~16 pts, 3% → ~28 pts, 10% → 40 pts."""
    if rate <= 0:
        return 0.0
    return round(min(40.0, 40.0 * math.log1p(rate) / math.log1p(10.0)), 2)


def _audience_size_score(subscribers: int) -> float:
    """0-30 pts tiered by subscriber count."""
    if subscribers < 1_000:
        return 0.0
    elif subscribers < 10_000:
        return 5.0
    elif subscribers < 50_000:
        return 15.0
    elif subscribers < 200_000:
        return 22.0
    elif subscribers < 500_000:
        return 26.0
    else:
        return 30.0


def score_influencers(state: GraphState) -> dict:
    """
    Node 4: Score each filtered channel.
    - Deterministic: engagement score + audience size score
    - LLM: Windsor.ai relevance score (0-30) via Claude structured output
    Persists results to SQLite. Returns sorted by composite_score descending.
    """
    llm = get_llm(temperature=0.2)
    structured_llm = llm.with_structured_output(ScoringResult, method="json_schema")

    db = Database()
    errors: list[str] = []
    scored: list[dict] = []
    for ch in state.get("filtered_channels", []):
        cid = ch.get("channel_id", "unknown")
        engagement_pts = _engagement_score(ch.get("engagement_rate", 0.0))
        size_pts = _audience_size_score(ch.get("subscriber_count", 0))

        prompt = WINDSOR_RELEVANCE_PROMPT.format(
            title=ch.get("channel_title", ""),
            description=(ch.get("description", "") or "")[:500],
            keywords=", ".join(ch.get("keywords", [])[:10]),
            video_titles=", ".join(ch.get("recent_video_titles", [])[:5]),
            subscribers=ch.get("subscriber_count", 0),
            engagement_rate=ch.get("engagement_rate", 0.0),
        )

        try:
            result: ScoringResult = _invoke_with_backoff(structured_llm, prompt)
            relevance_pts = result.relevance_score
            rationale = result.relevance_rationale
            niche_tags = result.niche_tags
        except Exception as e:
            err_msg = f"[score_influencers] LLM scoring failed for {cid}: {e}"
            print(err_msg)
            errors.append(err_msg)
            relevance_pts = 0.0
            rationale = "Scoring unavailable"
            niche_tags = []

        composite = round(engagement_pts + size_pts + relevance_pts, 2)

        scored_ch = {
            "channel_id": ch["channel_id"],
            "channel_title": ch["channel_title"],
            "engagement_rate": ch.get("engagement_rate", 0.0),
            "subscriber_count": ch.get("subscriber_count", 0),
            "composite_score": composite,
            "score_breakdown": {
                "engagement": engagement_pts,
                "audience_size": size_pts,
                "relevance": relevance_pts,
            },
            "relevance_rationale": rationale,
            "niche_tags": niche_tags,
        }
        scored.append(scored_ch)

        try:
            db.upsert_scored_influencer(scored_ch)
        except Exception as e:
            err_msg = f"[score_influencers] DB upsert failed for {cid}: {e}"
            print(err_msg)
            errors.append(err_msg)

    scored.sort(key=lambda x: x["composite_score"], reverse=True)
    print(f"[score_influencers] Scored {len(scored)} channels ({len(errors)} errors)")
    return {
        "scored_influencers": scored,
        "error_log": errors,
        "current_phase": "scoring_complete",
    }
