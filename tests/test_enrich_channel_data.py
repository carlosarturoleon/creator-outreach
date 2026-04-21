"""Tests for src/nodes/enrich_channel_data.py"""
import pytest
from unittest.mock import MagicMock, patch, call
from src.nodes.enrich_channel_data import enrich_channel_data


def _minimal_channel(channel_id="UC_001", search_keyword="google analytics"):
    return {
        "channel_id": channel_id,
        "channel_title": "Test Channel",
        "description": "Google Analytics tutorials.",
        "search_keyword": search_keyword,
    }


def _cached_db_row(channel_id="UC_001"):
    """Simulates what get_cached_channels returns — a fully enriched DB row."""
    return {
        "channel_id": channel_id,
        "channel_title": "Test Channel",
        "description": "Google Analytics tutorials.",
        "subscriber_count": 20_000,
        "total_view_count": 400_000,
        "video_count": 80,
        "country": "US",
        "default_language": "en",
        "keywords": ["google analytics", "ga4"],
        "avg_views_per_video": 5_000.0,
        "avg_likes_per_video": 200.0,
        "avg_comments_per_video": 50.0,
        "engagement_rate": 3.0,
        "upload_frequency_days": 7.0,
        "recent_video_titles": ["GA4 Guide"],
        "search_keyword": "old keyword",  # will be overwritten by current run
        "last_updated_at": "2026-04-20T10:00:00+00:00",
    }


def _api_channel_stats(channel_id="UC_001"):
    return {
        "channel_id": channel_id,
        "subscriber_count": 25_000,
        "total_view_count": 500_000,
        "video_count": 100,
        "country": "US",
        "default_language": "en",
        "keywords": ["analytics"],
        "description": "Google Analytics tutorials.",
    }


def _api_video_stats():
    return {
        "avg_views_per_video": 5_000.0,
        "avg_likes_per_video": 200.0,
        "avg_comments_per_video": 50.0,
        "engagement_rate": 3.5,
        "upload_frequency_days": 7.0,
        "recent_video_titles": ["GA4 Setup", "Attribution Guide"],
    }


def _state(channels):
    return {"pre_filtered_channels": channels}


# ---------------------------------------------------------------------------
# Cache hit — no API calls
# ---------------------------------------------------------------------------

@patch("src.nodes.enrich_channel_data.YouTubeClient")
@patch("src.nodes.enrich_channel_data.Database")
def test_uses_cache_for_fresh_channel(MockDB, MockYT):
    mock_db = MagicMock()
    mock_db.get_cached_channels.return_value = {"UC_001": _cached_db_row()}
    MockDB.return_value = mock_db

    result = enrich_channel_data(_state([_minimal_channel()]))

    assert len(result["enriched_channels"]) == 1
    MockYT.assert_not_called()  # YouTubeClient never instantiated


@patch("src.nodes.enrich_channel_data.YouTubeClient")
@patch("src.nodes.enrich_channel_data.Database")
def test_merges_search_keyword_from_current_run(MockDB, MockYT):
    mock_db = MagicMock()
    mock_db.get_cached_channels.return_value = {"UC_001": _cached_db_row()}
    MockDB.return_value = mock_db

    ch = _minimal_channel(search_keyword="new keyword from this run")
    result = enrich_channel_data(_state([ch]))

    enriched = result["enriched_channels"][0]
    assert enriched["search_keyword"] == "new keyword from this run"


# ---------------------------------------------------------------------------
# Cache miss — API called
# ---------------------------------------------------------------------------

@patch("src.nodes.enrich_channel_data.YouTubeClient")
@patch("src.nodes.enrich_channel_data.Database")
def test_fetches_from_api_for_stale_channel(MockDB, MockYT):
    mock_db = MagicMock()
    mock_db.get_cached_channels.return_value = {}  # no cache
    MockDB.return_value = mock_db

    mock_yt = MagicMock()
    mock_yt.get_channel_stats.return_value = [_api_channel_stats()]
    mock_yt.get_channel_video_stats.return_value = _api_video_stats()
    MockYT.return_value = mock_yt

    result = enrich_channel_data(_state([_minimal_channel()]))

    assert len(result["enriched_channels"]) == 1
    mock_yt.get_channel_stats.assert_called_once()
    mock_yt.get_channel_video_stats.assert_called_once()


@patch("src.nodes.enrich_channel_data.YouTubeClient")
@patch("src.nodes.enrich_channel_data.Database")
def test_upsert_channel_called_for_stale(MockDB, MockYT):
    mock_db = MagicMock()
    mock_db.get_cached_channels.return_value = {}
    MockDB.return_value = mock_db

    mock_yt = MagicMock()
    mock_yt.get_channel_stats.return_value = [_api_channel_stats()]
    mock_yt.get_channel_video_stats.return_value = _api_video_stats()
    MockYT.return_value = mock_yt

    enrich_channel_data(_state([_minimal_channel()]))
    mock_db.upsert_channel.assert_called_once()


# ---------------------------------------------------------------------------
# Partial cache
# ---------------------------------------------------------------------------

@patch("src.nodes.enrich_channel_data.YouTubeClient")
@patch("src.nodes.enrich_channel_data.Database")
def test_partial_cache_api_called_only_for_stale(MockDB, MockYT):
    mock_db = MagicMock()
    # UC_001 is cached, UC_002 is stale
    mock_db.get_cached_channels.return_value = {"UC_001": _cached_db_row("UC_001")}
    MockDB.return_value = mock_db

    mock_yt = MagicMock()
    mock_yt.get_channel_stats.return_value = [_api_channel_stats("UC_002")]
    mock_yt.get_channel_video_stats.return_value = _api_video_stats()
    MockYT.return_value = mock_yt

    channels = [
        _minimal_channel("UC_001"),
        _minimal_channel("UC_002"),
    ]
    result = enrich_channel_data(_state(channels))

    assert len(result["enriched_channels"]) == 2
    # get_channel_stats was called with only the stale ID
    call_args = mock_yt.get_channel_stats.call_args[0][0]
    assert "UC_002" in call_args
    assert "UC_001" not in call_args


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

@patch("src.nodes.enrich_channel_data.YouTubeClient")
@patch("src.nodes.enrich_channel_data.Database")
def test_api_error_populates_error_log(MockDB, MockYT):
    mock_db = MagicMock()
    mock_db.get_cached_channels.return_value = {}
    MockDB.return_value = mock_db

    mock_yt = MagicMock()
    mock_yt.get_channel_stats.side_effect = Exception("quota exceeded")
    mock_yt._empty_video_stats.return_value = {
        "avg_views_per_video": 0.0,
        "avg_likes_per_video": 0.0,
        "avg_comments_per_video": 0.0,
        "engagement_rate": 0.0,
        "upload_frequency_days": 0.0,
        "recent_video_titles": [],
    }
    mock_yt.get_channel_video_stats.return_value = mock_yt._empty_video_stats()
    MockYT.return_value = mock_yt

    result = enrich_channel_data(_state([_minimal_channel()]))

    assert len(result["error_log"]) > 0
    assert "quota exceeded" in result["error_log"][0]


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------

@patch("src.nodes.enrich_channel_data.YouTubeClient")
@patch("src.nodes.enrich_channel_data.Database")
def test_empty_input_returns_empty(MockDB, MockYT):
    mock_db = MagicMock()
    mock_db.get_cached_channels.return_value = {}
    MockDB.return_value = mock_db

    result = enrich_channel_data(_state([]))

    assert result["enriched_channels"] == []
    MockYT.assert_not_called()


# ---------------------------------------------------------------------------
# Return shape
# ---------------------------------------------------------------------------

@patch("src.nodes.enrich_channel_data.YouTubeClient")
@patch("src.nodes.enrich_channel_data.Database")
def test_return_keys(MockDB, MockYT):
    mock_db = MagicMock()
    mock_db.get_cached_channels.return_value = {}
    MockDB.return_value = mock_db

    result = enrich_channel_data(_state([]))
    assert "enriched_channels" in result
    assert "error_log" in result
    assert "current_phase" in result


@patch("src.nodes.enrich_channel_data.YouTubeClient")
@patch("src.nodes.enrich_channel_data.Database")
def test_current_phase(MockDB, MockYT):
    mock_db = MagicMock()
    mock_db.get_cached_channels.return_value = {}
    MockDB.return_value = mock_db

    result = enrich_channel_data(_state([]))
    assert result["current_phase"] == "enrichment_complete"
