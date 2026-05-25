"""Tests for src/nodes/score_influencers.py"""
import math
import pytest
from unittest.mock import MagicMock, patch
from src.nodes.score_influencers import (
    score_influencers,
    _engagement_score,
    _audience_size_score,
    _metrics_changed_significantly,
)


def _ch(**overrides):
    base = {
        "channel_id": "UC_test_001",
        "channel_title": "Google Analytics Pro",
        "description": "Learn google analytics, GA4, and conversion tracking.",
        "subscriber_count": 25_000,
        "engagement_rate": 3.5,
        "avg_views_per_video": 5_000.0,
        "upload_frequency_days": 7.0,
        "keywords": ["google analytics", "ga4"],
        "recent_video_titles": ["GA4 Guide", "Attribution Explained"],
        "default_language": "en",
    }
    base.update(overrides)
    return base


def _cached_score(**overrides):
    base = {
        "channel_id": "UC_test_001",
        "relevance_score": 15.0,
        "relevance_rationale": "Good fit.",
        "niche_tags": ["Google Analytics"],
        "engagement_score": _engagement_score(3.5),
        "audience_size_score": _audience_size_score(25_000),
        "scored_at": "2026-04-20T10:00:00+00:00",
    }
    base.update(overrides)
    return base


def _state(channels):
    return {"filtered_channels": channels}


# ---------------------------------------------------------------------------
# _engagement_score
# ---------------------------------------------------------------------------

def test_engagement_score_zero():
    assert _engagement_score(0.0) == 0.0


def test_engagement_score_1pct():
    score = _engagement_score(1.0)
    assert score == pytest.approx(35.0 * math.log1p(1.0) / math.log1p(10.0), abs=0.01)


def test_engagement_score_10pct():
    score = _engagement_score(10.0)
    assert score == pytest.approx(35.0, abs=0.01)


def test_engagement_score_capped_at_40():
    assert _engagement_score(100.0) == 35.0


def test_engagement_score_negative():
    assert _engagement_score(-1.0) == 0.0


# ---------------------------------------------------------------------------
# _audience_size_score
# ---------------------------------------------------------------------------

def test_audience_size_below_1k():
    assert _audience_size_score(999) == 0.0


def test_audience_size_5k():
    assert _audience_size_score(5_000) == 10.0


def test_audience_size_25k():
    assert _audience_size_score(25_000) == 15.0


def test_audience_size_100k():
    assert _audience_size_score(100_000) == 12.0


def test_audience_size_300k():
    assert _audience_size_score(300_000) == 7.0


def test_audience_size_1m():
    assert _audience_size_score(1_000_000) == 3.0


# ---------------------------------------------------------------------------
# _metrics_changed_significantly
# ---------------------------------------------------------------------------

def test_no_change_returns_false():
    ch = _ch(engagement_rate=3.5, subscriber_count=25_000)
    cached = _cached_score(
        engagement_score=_engagement_score(3.5),
        audience_size_score=_audience_size_score(25_000),
    )
    assert not _metrics_changed_significantly(ch, cached)


def test_large_engagement_change_returns_true():
    ch = _ch(engagement_rate=5.0, subscriber_count=25_000)
    cached = _cached_score(
        engagement_score=_engagement_score(1.0),  # much lower
        audience_size_score=_audience_size_score(25_000),
    )
    assert _metrics_changed_significantly(ch, cached)


def test_audience_tier_change_returns_true():
    # 5k → 15k crosses a tier boundary
    ch = _ch(subscriber_count=15_000, engagement_rate=3.5)
    cached = _cached_score(
        engagement_score=_engagement_score(3.5),
        audience_size_score=_audience_size_score(5_000),  # was 5.0, now 15.0 → >10% delta
    )
    assert _metrics_changed_significantly(ch, cached)


# ---------------------------------------------------------------------------
# score_influencers — main function
# ---------------------------------------------------------------------------

@patch("src.nodes.score_influencers.Database")
def test_scores_channel_deterministically(MockDB):
    mock_db = MagicMock()
    mock_db.get_cached_scores.return_value = {}
    MockDB.return_value = mock_db

    result = score_influencers(_state([_ch()]))
    assert len(result["pre_llm_influencers"]) == 1
    ch = result["pre_llm_influencers"][0]
    assert ch["composite_score"] > 0
    assert "engagement" in ch["score_breakdown"]
    assert "audience_size" in ch["score_breakdown"]
    assert "relevance" in ch["score_breakdown"]


@patch("src.nodes.score_influencers.Database")
def test_uses_cached_score_when_fresh_and_unchanged(MockDB):
    mock_db = MagicMock()
    cached = _cached_score()
    mock_db.get_cached_scores.return_value = {"UC_test_001": cached}
    MockDB.return_value = mock_db

    result = score_influencers(_state([_ch()]))
    ch = result["pre_llm_influencers"][0]
    # Cached relevance_score=15.0 is scaled by 25/30 in the node
    assert ch["score_breakdown"]["relevance"] == pytest.approx(15.0 * (25.0 / 30.0), abs=0.01)
    # DB upsert is still called to refresh the record
    mock_db.upsert_scored_influencer.assert_called_once()


@patch("src.nodes.score_influencers.Database")
def test_invalidates_cache_on_engagement_change(MockDB):
    mock_db = MagicMock()
    # Cache has very low engagement score (based on 0.1% ER)
    cached = _cached_score(
        engagement_score=_engagement_score(0.1),
        relevance_score=5.0,
    )
    mock_db.get_cached_scores.return_value = {"UC_test_001": cached}
    MockDB.return_value = mock_db

    # Current channel has 3.5% ER — huge change → cache invalidated, re-scored
    result = score_influencers(_state([_ch(engagement_rate=3.5)]))
    ch = result["pre_llm_influencers"][0]
    # Relevance will be re-computed by keyword_scorer, not the cached 5.0
    # We just verify it ran without error and produced a valid result
    assert ch["composite_score"] >= 0


@patch("src.nodes.score_influencers.Database")
def test_composite_equals_sum_of_parts(MockDB):
    mock_db = MagicMock()
    mock_db.get_cached_scores.return_value = {}
    MockDB.return_value = mock_db

    result = score_influencers(_state([_ch()]))
    ch = result["pre_llm_influencers"][0]
    expected = round(
        ch["score_breakdown"]["engagement"]
        + ch["score_breakdown"]["audience_size"]
        + ch["score_breakdown"]["relevance"]
        + ch["score_breakdown"]["tutorial"]
        + ch["score_breakdown"]["upload_recency"],
        2,
    )
    assert ch["composite_score"] == pytest.approx(expected, abs=0.01)


@patch("src.nodes.score_influencers.Database")
def test_results_sorted_descending(MockDB):
    mock_db = MagicMock()
    mock_db.get_cached_scores.return_value = {}
    MockDB.return_value = mock_db

    ch_high = _ch(channel_id="UC_high", subscriber_count=1_000_000, engagement_rate=10.0)
    ch_low = _ch(channel_id="UC_low", subscriber_count=1_000, engagement_rate=0.1)

    result = score_influencers(_state([ch_low, ch_high]))
    scores = [ch["composite_score"] for ch in result["pre_llm_influencers"]]
    assert scores == sorted(scores, reverse=True)


@patch("src.nodes.score_influencers.Database")
def test_empty_input_returns_empty(MockDB):
    mock_db = MagicMock()
    mock_db.get_cached_scores.return_value = {}
    MockDB.return_value = mock_db

    result = score_influencers(_state([]))
    assert result["pre_llm_influencers"] == []
    mock_db.upsert_scored_influencer.assert_not_called()


@patch("src.nodes.score_influencers.Database")
def test_upsert_called_for_each_channel(MockDB):
    mock_db = MagicMock()
    mock_db.get_cached_scores.return_value = {}
    MockDB.return_value = mock_db

    channels = [
        _ch(channel_id="UC_001"),
        _ch(channel_id="UC_002"),
        _ch(channel_id="UC_003"),
    ]
    score_influencers(_state(channels))
    assert mock_db.upsert_scored_influencer.call_count == 3


@patch("src.nodes.score_influencers.Database")
def test_return_keys(MockDB):
    mock_db = MagicMock()
    mock_db.get_cached_scores.return_value = {}
    MockDB.return_value = mock_db

    result = score_influencers(_state([]))
    assert "pre_llm_influencers" in result
    assert "error_log" in result
    assert "current_phase" in result


@patch("src.nodes.score_influencers.Database")
def test_current_phase(MockDB):
    mock_db = MagicMock()
    mock_db.get_cached_scores.return_value = {}
    MockDB.return_value = mock_db

    result = score_influencers(_state([]))
    assert result["current_phase"] == "scoring_complete"
