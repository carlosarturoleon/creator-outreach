"""
Deterministic keyword-based relevance scoring for influencer candidates for your affiliate program.

Replaces LLM scoring with a rule-based system using weighted keyword matching
against the channel description, title, keywords, and recent video titles.

Scoring tiers (description-focused):
  HIGH_VALUE   = 3 pts each  — direct product use-case signals
  MEDIUM_VALUE = 2 pts each  — paid advertising / performance marketing
  LOW_VALUE    = 1 pt each   — general digital marketing

Composite score (0-30, scaled to match the old LLM relevance range):
  keyword_pts normalized to 0-30, capped at max_scaled_pts.

Negative keywords cause immediate disqualification (score = 0).

All keyword lists, thresholds, and tag mappings are loaded from pipeline_config.yaml.
"""

from __future__ import annotations

from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Load pipeline_config.yaml once at module import time
# ---------------------------------------------------------------------------
_CONFIG_PATH = Path(__file__).parent.parent.parent / "pipeline_config.yaml"

with open(_CONFIG_PATH) as _f:
    _cfg = yaml.safe_load(_f)

_kw_cfg = _cfg["keyword_scoring"]

MAX_RAW_PTS: int = _kw_cfg["max_raw_pts"]
MAX_SCALED_PTS: float = float(_kw_cfg["max_scaled_pts"])
MAX_NICHE_TAGS: int = _kw_cfg["max_niche_tags"]

_FIT = _kw_cfg["fit_thresholds"]
FIT_STRONG: int = _FIT["strong"]
FIT_GOOD: int = _FIT["good"]
FIT_MODERATE: int = _FIT["moderate"]

HIGH_VALUE: list[str] = _kw_cfg["high_value_keywords"]
MEDIUM_VALUE: list[str] = _kw_cfg["medium_value_keywords"]
LOW_VALUE: list[str] = _kw_cfg["low_value_keywords"]
NEGATIVE_KEYWORDS: list[str] = _kw_cfg["negative_keywords"]
_NICHE_TAG_MAP: dict[str, str] = _kw_cfg["niche_tag_map"]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _build_text_blob(ch: dict) -> str:
    """Concatenate all text fields into a single lowercase string for matching."""
    parts = [
        ch.get("description", "") or "",
        ch.get("channel_title", "") or "",
        " ".join(ch.get("keywords", []) or []),
        " ".join(ch.get("recent_video_titles", []) or []),
    ]
    return " ".join(parts).lower()


def _keyword_points(text: str) -> tuple[int, list[str]]:
    """Return (total_points, matched_keyword_list)."""
    matched: list[str] = []
    points = 0
    for kw in HIGH_VALUE:
        if kw in text:
            matched.append(kw)
            points += 3
    for kw in MEDIUM_VALUE:
        if kw in text and kw not in matched:
            matched.append(kw)
            points += 2
    for kw in LOW_VALUE:
        if kw in text and kw not in matched:
            matched.append(kw)
            points += 1
    return points, matched


def _upload_freq_score(upload_frequency_days: float) -> float:
    """0-10 pts based on upload cadence."""
    if upload_frequency_days <= 0:
        return 0.0
    if upload_frequency_days <= 7:
        return 10.0   # weekly or more
    if upload_frequency_days <= 14:
        return 5.0    # bi-weekly
    if upload_frequency_days <= 30:
        return 2.0    # monthly
    return 0.0


def _view_ratio_score(avg_views: float, subscribers: int) -> float:
    """0-10 pts based on avg_views / subscribers ratio (capped)."""
    if subscribers <= 0 or avg_views <= 0:
        return 0.0
    ratio = (avg_views / subscribers) * 100  # as percentage
    return min(10.0, ratio)


def _infer_niche_tags(matched_keywords: list[str]) -> list[str]:
    """Map matched keywords to human-readable niche tags (deduped, max MAX_NICHE_TAGS)."""
    seen: set[str] = set()
    tags: list[str] = []
    for kw in matched_keywords:
        tag = _NICHE_TAG_MAP.get(kw)
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
        if len(tags) >= MAX_NICHE_TAGS:
            break
    return tags


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_channel_relevance(ch: dict) -> dict:
    """
    Score a single channel dict deterministically.

    Returns a dict with keys matching the old LLM ScoringResult:
      relevance_score       float  0-30
      relevance_rationale   str
      niche_tags            list[str]
    Plus an extra key:
      keyword_score_raw     int  (raw points before composite weighting)
    """
    text = _build_text_blob(ch)

    # Disqualify immediately if negative keyword found
    for neg in NEGATIVE_KEYWORDS:
        if neg in text:
            return {
                "relevance_score": 0.0,
                "relevance_rationale": f"Disqualified: negative keyword '{neg}' found.",
                "niche_tags": [],
                "keyword_score_raw": 0,
            }

    keyword_pts, matched = _keyword_points(text)

    # Score of 0 → hard skip (no relevant keywords at all)
    if keyword_pts == 0:
        return {
            "relevance_score": 0.0,
            "relevance_rationale": "No relevant keywords found in channel content.",
            "niche_tags": [],
            "keyword_score_raw": 0,
        }

    relevance_score = round(min(MAX_SCALED_PTS, keyword_pts * (MAX_SCALED_PTS / MAX_RAW_PTS)), 2)

    niche_tags = _infer_niche_tags(matched)

    # Human-readable rationale
    if keyword_pts >= FIT_STRONG:
        fit = "strong fit"
    elif keyword_pts >= FIT_GOOD:
        fit = "good fit"
    elif keyword_pts >= FIT_MODERATE:
        fit = "moderate fit"
    else:
        fit = "weak fit"

    top_kws = ", ".join(matched[:4]) if matched else "general marketing"
    rationale = f"{fit.capitalize()} for affiliate program — matched keywords: {top_kws}."

    return {
        "relevance_score": relevance_score,
        "relevance_rationale": rationale,
        "niche_tags": niche_tags,
        "keyword_score_raw": keyword_pts,
    }
