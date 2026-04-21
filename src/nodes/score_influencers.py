import math

from src.logger import get_logger
from src.state import GraphState
from src.db.database import Database
from src.scoring.keyword_scorer import score_channel_relevance

log = get_logger(__name__)

_SCORE_CACHE_DAYS = 30           # reuse cached score if scored within this many days
_METRICS_CHANGE_THRESHOLD = 0.10  # re-score if engagement or audience tier changed >10%


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


def _metrics_changed_significantly(ch: dict, cached: dict) -> bool:
    """Return True if engagement or audience tier score shifted by more than the threshold."""
    def pct_delta(new: float, old: float) -> float:
        return abs(new - old) / old if old else 0.0

    eng_score_new = _engagement_score(ch.get("engagement_rate", 0.0))
    eng_score_old = cached.get("engagement_score", 0.0)
    size_score_new = _audience_size_score(ch.get("subscriber_count", 0))
    size_score_old = cached.get("audience_size_score", 0.0)

    return (
        pct_delta(eng_score_new, eng_score_old) > _METRICS_CHANGE_THRESHOLD
        or pct_delta(size_score_new, size_score_old) > _METRICS_CHANGE_THRESHOLD
    )


def score_influencers(state: GraphState) -> dict:
    """
    Node 4: Score each filtered channel.

    Scoring breakdown (max 100 pts):
      - Engagement score   0-40  (log-scale of engagement rate)
      - Audience size      0-30  (tiered by subscriber count)
      - Keyword relevance  0-30  (deterministic keyword scoring against
                                  description, title, channel keywords,
                                  and recent video titles)

    Keyword relevance replaces the LLM — no API calls, fully deterministic,
    instant, and reproducible. Channels matching negative keywords
    (crypto, gaming, lifestyle, etc.) receive relevance_score = 0.

    Cached scores (within _SCORE_CACHE_DAYS) are reused unless metrics
    changed significantly, saving repeated computation.

    Results are persisted to SQLite and returned sorted by composite_score desc.
    """
    db = Database()
    errors: list[str] = []
    channels = state.get("filtered_channels", [])
    log.info("score_influencers START — scoring %d channels", len(channels))

    # Cache lookup
    all_ids = [ch.get("channel_id", "") for ch in channels]
    cached_scores = db.get_cached_scores(all_ids, max_age_days=_SCORE_CACHE_DAYS)

    scored: list[dict] = []
    cache_hits = 0
    fresh_scored = 0

    for i, ch in enumerate(channels, 1):
        cid = ch.get("channel_id", "unknown")
        engagement_pts = _engagement_score(ch.get("engagement_rate", 0.0))
        size_pts = _audience_size_score(ch.get("subscriber_count", 0))

        cached = cached_scores.get(cid)
        use_cache = cached is not None and not _metrics_changed_significantly(ch, cached)

        if use_cache:
            relevance_pts = cached["relevance_score"]
            rationale = cached.get("relevance_rationale", "")
            niche_tags = cached.get("niche_tags", [])
            cache_hits += 1
            log.info("  [%d/%d] Cache hit: %s (relevance=%.1f)",
                     i, len(channels), ch.get("channel_title", cid), relevance_pts)
        else:
            kw_result = score_channel_relevance(ch)
            relevance_pts = kw_result["relevance_score"]
            rationale = kw_result["relevance_rationale"]
            niche_tags = kw_result["niche_tags"]
            fresh_scored += 1
            log.info("  [%d/%d] Scored: %s — kw_raw=%d, relevance=%.1f",
                     i, len(channels), ch.get("channel_title", cid),
                     kw_result.get("keyword_score_raw", 0), relevance_pts)

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

        log.info("    score=%.1f (eng=%.1f + size=%.1f + relevance=%.1f)",
                 composite, engagement_pts, size_pts, relevance_pts)
        try:
            db.upsert_scored_influencer(scored_ch)
        except Exception as e:
            err_msg = f"[score_influencers] DB upsert failed for {cid}: {e}"
            log.error("  DB upsert failed for %s: %s", cid, e)
            errors.append(err_msg)

    scored.sort(key=lambda x: x["composite_score"], reverse=True)
    log.info("score_influencers DONE — %d scored (%d keyword-scored, %d cache hits), %d errors",
             len(scored), fresh_scored, cache_hits, len(errors))
    return {
        "scored_influencers": scored,
        "error_log": errors,
        "current_phase": "scoring_complete",
    }
