import math
from pathlib import Path

import yaml

from src.logger import get_logger
from src.state import GraphState
from src.db.database import Database
from src.scoring.keyword_scorer import score_channel_relevance

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Load pipeline_config.yaml once at module import time
# ---------------------------------------------------------------------------
_CONFIG_PATH = Path(__file__).parent.parent.parent / "pipeline_config.yaml"

with open(_CONFIG_PATH) as _f:
    _cfg = yaml.safe_load(_f)

_sc = _cfg["scoring"]

_SCORE_CACHE_DAYS: int = _sc["cache_days"]
_METRICS_CHANGE_THRESHOLD: float = _sc["metrics_change_threshold"]
_MIN_SUBSCRIBERS: int = _sc["min_subscribers"]

_ENGAGEMENT_MAX: float = float(_sc["weights"]["engagement_max"])
_AUDIENCE_SIZE_MAX: float = float(_sc["weights"]["audience_size_max"])
_KW_RELEVANCE_MAX: float = float(_sc["weights"]["keyword_relevance_max"])
_TUTORIAL_MAX: float = float(_sc["weights"]["tutorial_signal_max"])
_UPLOAD_RECENCY_MAX: float = float(_sc["weights"]["upload_recency_max"])

_TUTORIAL_SIGNALS: list[str] = _sc["tutorial_signals"]
_TUTORIAL_PTS_PER_MATCH: float = float(_sc["tutorial_pts_per_match"])

# Pre-process tier lists (sort ascending by max bound, None = infinity)
_AUDIENCE_TIERS: list[dict] = _sc["audience_size_tiers"]
_RECENCY_TIERS: list[dict] = _sc["upload_recency_tiers"]

# keyword_scorer returns 0–30; scale to 0–keyword_relevance_max
_KW_SCALE_FACTOR: float = _KW_RELEVANCE_MAX / float(_cfg["keyword_scoring"]["max_scaled_pts"])


def _engagement_score(rate: float) -> float:
    """0–{max} pts using log scale."""
    if rate <= 0:
        return 0.0
    return round(min(_ENGAGEMENT_MAX, _ENGAGEMENT_MAX * math.log1p(rate) / math.log1p(10.0)), 2)


def _audience_size_score(subscribers: int) -> float:
    """0–{max} pts. Tiered from pipeline_config.yaml."""
    for tier in _AUDIENCE_TIERS:
        cap = tier["max_subscribers"]
        if cap is None or subscribers < cap:
            return float(tier["pts"])
    return float(_AUDIENCE_TIERS[-1]["pts"])


def _tutorial_score(recent_video_titles: list) -> float:
    """0–{max} pts. Counts tutorial-style signals in recent video titles."""
    if not recent_video_titles:
        return 0.0
    titles_text = " ".join(str(t) for t in recent_video_titles).lower()
    matches = sum(1 for sig in _TUTORIAL_SIGNALS if sig in titles_text)
    return round(min(_TUTORIAL_MAX, matches * _TUTORIAL_PTS_PER_MATCH), 2)


def _upload_recency_score(upload_frequency_days: float) -> float:
    """0–{max} pts based on upload cadence. Tiered from pipeline_config.yaml."""
    if upload_frequency_days <= 0:
        return 0.0
    for tier in _RECENCY_TIERS:
        cap = tier["max_days"]
        if cap is None or upload_frequency_days <= cap:
            return float(tier["pts"])
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
    Node 4: Score each filtered channel for affiliate program fit.

    Scoring breakdown (max 100 pts, weights from pipeline_config.yaml):
      - Engagement score   0-35  log-scale of engagement rate
      - Audience size      0-15  tiered; peak at 10k-50k (affiliate-hungry tier)
      - Keyword relevance  0-25  deterministic keyword matching (scaled from 0-30)
      - Tutorial signal    0-15  how-to / tutorial video titles
      - Upload recency     0-10  upload cadence

    Cached scores (within cache_days) are reused unless metrics changed
    significantly. Results persisted to SQLite, sorted by composite_score desc.
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
            relevance_pts = cached["relevance_score"] * _KW_SCALE_FACTOR
            rationale = cached.get("relevance_rationale", "")
            niche_tags = cached.get("niche_tags", [])
            cache_hits += 1
            log.info("  [%d/%d] Cache hit: %s (relevance=%.1f)",
                     i, len(channels), ch.get("channel_title", cid), relevance_pts)
        else:
            kw_result = score_channel_relevance(ch)
            relevance_pts = round(kw_result["relevance_score"] * _KW_SCALE_FACTOR, 2)
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
        "pre_llm_influencers": scored,
        "error_log": errors,
        "current_phase": "scoring_complete",
    }
