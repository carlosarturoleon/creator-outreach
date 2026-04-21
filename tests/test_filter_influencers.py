"""Tests for src/nodes/filter_influencers.py"""
import pytest
from unittest.mock import MagicMock, patch
from src.nodes.filter_influencers import filter_influencers


def _state(channels, min_subscribers=5_000, max_subscribers=0, min_engagement=1.0, languages=None):
    return {
        "enriched_channels": channels,
        "min_subscribers": min_subscribers,
        "max_subscribers": max_subscribers,
        "min_engagement_rate": min_engagement,
        "target_languages": languages or ["en"],
    }


def _ch(**overrides):
    base = {
        "channel_id": "UC_test",
        "channel_title": "Analytics Pro",
        "description": "Learn google analytics and attribution tracking.",
        "subscriber_count": 25_000,
        "engagement_rate": 3.5,
        "default_language": "en",
        "keywords": ["analytics"],
        "recent_video_titles": ["GA4 Guide"],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Passes
# ---------------------------------------------------------------------------

@patch("src.nodes.filter_influencers.Database")
def test_passes_valid_channel(MockDB):
    MockDB.return_value.mark_channels_filtered = MagicMock()
    result = filter_influencers(_state([_ch()]))
    assert len(result["filtered_channels"]) == 1


# ---------------------------------------------------------------------------
# Hard filter: subscribers
# ---------------------------------------------------------------------------

@patch("src.nodes.filter_influencers.Database")
def test_drops_below_min_subscribers(MockDB):
    MockDB.return_value.mark_channels_filtered = MagicMock()
    result = filter_influencers(_state([_ch(subscriber_count=4_999)]))
    assert len(result["filtered_channels"]) == 0


@patch("src.nodes.filter_influencers.Database")
def test_keeps_exactly_at_min_subscribers(MockDB):
    MockDB.return_value.mark_channels_filtered = MagicMock()
    result = filter_influencers(_state([_ch(subscriber_count=5_000)]))
    assert len(result["filtered_channels"]) == 1


@patch("src.nodes.filter_influencers.Database")
def test_drops_above_max_subscribers(MockDB):
    MockDB.return_value.mark_channels_filtered = MagicMock()
    result = filter_influencers(_state([_ch(subscriber_count=10_001)], max_subscribers=10_000))
    assert len(result["filtered_channels"]) == 0


@patch("src.nodes.filter_influencers.Database")
def test_keeps_exactly_at_max_subscribers(MockDB):
    MockDB.return_value.mark_channels_filtered = MagicMock()
    result = filter_influencers(_state([_ch(subscriber_count=10_000)], max_subscribers=10_000))
    assert len(result["filtered_channels"]) == 1


@patch("src.nodes.filter_influencers.Database")
def test_no_cap_when_max_subscribers_zero(MockDB):
    MockDB.return_value.mark_channels_filtered = MagicMock()
    result = filter_influencers(_state([_ch(subscriber_count=1_000_000)], max_subscribers=0))
    assert len(result["filtered_channels"]) == 1


# ---------------------------------------------------------------------------
# Hard filter: engagement
# ---------------------------------------------------------------------------

@patch("src.nodes.filter_influencers.Database")
def test_drops_below_min_engagement(MockDB):
    MockDB.return_value.mark_channels_filtered = MagicMock()
    result = filter_influencers(_state([_ch(engagement_rate=0.5)]))
    assert len(result["filtered_channels"]) == 0


@patch("src.nodes.filter_influencers.Database")
def test_keeps_exactly_at_min_engagement(MockDB):
    MockDB.return_value.mark_channels_filtered = MagicMock()
    result = filter_influencers(_state([_ch(engagement_rate=1.0)]))
    assert len(result["filtered_channels"]) == 1


# ---------------------------------------------------------------------------
# Hard filter: language
# ---------------------------------------------------------------------------

@patch("src.nodes.filter_influencers.Database")
def test_drops_wrong_language(MockDB):
    MockDB.return_value.mark_channels_filtered = MagicMock()
    result = filter_influencers(_state([_ch(default_language="de")], languages=["en"]))
    assert len(result["filtered_channels"]) == 0


@patch("src.nodes.filter_influencers.Database")
def test_keeps_matching_language(MockDB):
    MockDB.return_value.mark_channels_filtered = MagicMock()
    result = filter_influencers(_state([_ch(default_language="en")], languages=["en"]))
    assert len(result["filtered_channels"]) == 1


@patch("src.nodes.filter_influencers.Database")
def test_keeps_unknown_language_permissive(MockDB):
    MockDB.return_value.mark_channels_filtered = MagicMock()
    # No default_language → permissive, keep it
    result = filter_influencers(_state([_ch(default_language=None)], languages=["en"]))
    assert len(result["filtered_channels"]) == 1


@patch("src.nodes.filter_influencers.Database")
def test_keeps_empty_string_language_permissive(MockDB):
    MockDB.return_value.mark_channels_filtered = MagicMock()
    result = filter_influencers(_state([_ch(default_language="")], languages=["en"]))
    assert len(result["filtered_channels"]) == 1


# ---------------------------------------------------------------------------
# Soft filter: niche keywords
# ---------------------------------------------------------------------------

@patch("src.nodes.filter_influencers.Database")
def test_drops_channel_no_niche_keywords_anywhere(MockDB):
    MockDB.return_value.mark_channels_filtered = MagicMock()
    ch = _ch(
        description="I make videos about cooking and travel.",
        keywords=["food", "travel"],
        recent_video_titles=["Best Pizza Recipe", "Trip to Italy"],
        channel_title="Foodie Vlog",
    )
    result = filter_influencers(_state([ch]))
    assert len(result["filtered_channels"]) == 0


@patch("src.nodes.filter_influencers.Database")
def test_passes_niche_keyword_in_video_titles(MockDB):
    MockDB.return_value.mark_channels_filtered = MagicMock()
    ch = _ch(
        description="General business content.",
        keywords=[],
        recent_video_titles=["Google Analytics for Beginners"],
        channel_title="Business Channel",
    )
    result = filter_influencers(_state([ch]))
    assert len(result["filtered_channels"]) == 1


# ---------------------------------------------------------------------------
# DB interaction
# ---------------------------------------------------------------------------

@patch("src.nodes.filter_influencers.Database")
def test_calls_mark_channels_filtered_with_ids(MockDB):
    mock_instance = MagicMock()
    MockDB.return_value = mock_instance
    result = filter_influencers(_state([_ch(channel_id="UC_abc")]))
    mock_instance.mark_channels_filtered.assert_called_once_with(["UC_abc"])


@patch("src.nodes.filter_influencers.Database")
def test_no_db_call_when_nothing_passes(MockDB):
    mock_instance = MagicMock()
    MockDB.return_value = mock_instance
    # Channel will fail subscriber filter
    result = filter_influencers(_state([_ch(subscriber_count=0)]))
    mock_instance.mark_channels_filtered.assert_not_called()


# ---------------------------------------------------------------------------
# Return shape
# ---------------------------------------------------------------------------

@patch("src.nodes.filter_influencers.Database")
def test_return_keys(MockDB):
    MockDB.return_value.mark_channels_filtered = MagicMock()
    result = filter_influencers(_state([]))
    assert "filtered_channels" in result
    assert "current_phase" in result


@patch("src.nodes.filter_influencers.Database")
def test_current_phase(MockDB):
    MockDB.return_value.mark_channels_filtered = MagicMock()
    result = filter_influencers(_state([]))
    assert result["current_phase"] == "filtering_complete"
