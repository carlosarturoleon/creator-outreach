import math

from src.logger import get_logger
from src.state import GraphState
from src.db.database import Database
from src.scoring.keyword_scorer import score_channel_relevance

log = get_logger(__name__)

_SCORE_CACHE_DAYS = 30           # reuse cached score if scored within this many days
_METRICS_CHANGE_THRESHOLD = 0.10  # re-score if engagement or audience tier changed >10%
_MIN_SUBSCRIBERS = 1_000         # hard floor — channels below this score 0 (no real audience)

# Tutorial/teaching intent signals — matched against recent_video_titles only
_TUTORIAL_SIGNALS: list[str] = [
    "how to", "tutorial", "step by step", "guide", "setup", "set up",
    "course", "beginner", "learn", "walkthrough", "explained", "for beginners",
    "masterclass", "training", "getting started",
]


def _engagement_score(rate: float) -> float:
    """0-35 pts using log scale. At 1% ER → ~14 pts, 3% → ~24 pts, 10% → 35 pts."""
    if rate <= 0:
        return 0.0
    return round(min(35.0, 35.0 * math.log1p(rate) / math.log1p(10.0)), 2)


def _audience_size_score(subscribers: int) -> float:
    """0-15 pts. Peak at 10k-50k (sweet spot for affiliate-hungry creators).
    Large channels score lower — they rarely bother with affiliate commissions.
    """
    if subscribers < 1_000:
        return 0.0
    elif subscribers < 5_000:
        return 3.0
    elif subscribers < 10_000:
        return 10.0
    elif subscribers < 50_000:
        return 15.0   # sweet spot
    elif subscribers < 200_000:
        return 12.0
    elif subscribers < 500_000:
        return 7.0
    else:
        return 3.0


def _tutorial_score(recent_video_titles: list) -> float:
    """0-15 pts. Counts how many recent video titles contain teaching/how-to signals.
    3+ tutorial-style titles → full 15 pts. Each match adds 5 pts (capped).
    """
    if not recent_video_titles:
        return 0.0
    titles_text = " ".join(str(t) for t in recent_video_titles).lower()
    matches = sum(1 for sig in _TUTORIAL_SIGNALS if sig in titles_text)
    return round(min(15.0, matches * 5.0), 2)


def _upload_recency_score(upload_frequency_days: float) -> float:
    """0-10 pts based on upload cadence."""
    if upload_frequency_days <= 0:
        return 0.0
    if upload_frequency_days <= 7:
        return 10.0
    if upload_frequency_days <= 14:
        return 7.0
    if upload_frequency_days <= 30:
        return 3.0
    return 0.0


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
    Node 4: Score each filtered channel for Windsor.ai affiliate program fit.

    Scoring breakdown (max 100 pts):
      - Engagement score   0-35  log-scale of engagement rate
      - Audience size      0-15  tiered; peak at 10k-50k (affiliate-hungry tier)
      - Keyword relevance  0-25  deterministic keyword matching (scaled from 0-30)
      - Tutorial signal    0-15  how-to / tutorial video titles (NEW)
      - Upload recency     0-10  upload cadence (standalone component)

    Tutorial signal is the strongest new predictor: creators who make
    "how to set up X" videos already recommend tools to their audience.

    Cached scores (within _SCORE_CACHE_DAYS) are reused unless metrics
    changed significantly. Results persisted to SQLite, sorted by composite_score desc.
    """
    db = Database()
    errors: list[str] = []
    channels = state.get("filtered_channels", [])
    log.info("score_influencers START — scoring %d channels", len(channels))

    all_ids = [ch.get("channel_id", "") for ch in channels]
    cached_scores = db.get_cached_scores(all_ids, max_age_days=_SCORE_CACHE_DAYS)

    scored: list[dict] = []
    cache_hits = 0
    fresh_scored = 0

    for i, ch in enumerate(channels, 1):
        cid = ch.get("channel_id", "unknown")
        subs = ch.get("subscriber_count", 0)

        if subs < _MIN_SUBSCRIBERS:
            log.info("  [%d/%d] Skip (<%d subs): %s",
                     i, len(channels), _MIN_SUBSCRIBERS, ch.get("channel_title", cid))
            continue

        engagement_pts = _engagement_score(ch.get("engagement_rate", 0.0))
        size_pts = _audience_size_score(subs)
        tutorial_pts = _tutorial_score(ch.get("recent_video_titles", []))
        upload_pts = _upload_recency_score(ch.get("upload_frequency_days", 0.0))

        cached = cached_scores.get(cid)
        use_cache = cached is not None and not _metrics_changed_significantly(ch, cached)

        if use_cache:
            relevance_pts = cached["relevance_score"] * (25.0 / 30.0)
            rationale = cached.get("relevance_rationale", "")
            niche_tags = cached.get("niche_tags", [])
            cache_hits += 1
            log.info("  [%d/%d] Cache hit: %s (relevance=%.1f)",
                     i, len(channels), ch.get("channel_title", cid), relevance_pts)
        else:
            kw_result = score_channel_relevance(ch)
            # keyword_scorer returns 0-30; scale to 0-25 for new weight
            relevance_pts = round(kw_result["relevance_score"] * (25.0 / 30.0), 2)
            rationale = kw_result["relevance_rationale"]
            niche_tags = kw_result["niche_tags"]
            fresh_scored += 1
            log.info("  [%d/%d] Scored: %s — kw_raw=%d, relevance=%.1f, tutorial=%.1f",
                     i, len(channels), ch.get("channel_title", cid),
                     kw_result.get("keyword_score_raw", 0), relevance_pts, tutorial_pts)

        composite = round(engagement_pts + size_pts + relevance_pts + tutorial_pts + upload_pts, 2)

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
                "tutorial": tutorial_pts,
                "upload_recency": upload_pts,
            },
            "relevance_rationale": rationale,
            "niche_tags": niche_tags,
        }
        scored.append(scored_ch)

        log.info("    score=%.1f (eng=%.1f + size=%.1f + rel=%.1f + tut=%.1f + up=%.1f)",
                 composite, engagement_pts, size_pts, relevance_pts, tutorial_pts, upload_pts)
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
