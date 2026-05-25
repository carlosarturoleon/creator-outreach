"""Tests for quota gate logic in src/nodes/discover_channels.py"""
from unittest.mock import MagicMock, patch

from src.nodes.discover_channels import discover_channels


def _state(**overrides):
    base = {
        "search_keywords": ["google analytics tutorial", "looker studio tutorial"],
        "max_results_per_keyword": 20,
        "max_seed_channels": 5,
        "quota_units_spent": 0,
        "quota_budget": 20_000,
        "enrich_quota_reserve": 0,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Quota gate: skip entirely when no allowance left
# ---------------------------------------------------------------------------

@patch("src.nodes.discover_channels.Database")
@patch("src.nodes.discover_channels.YouTubeClient")
def test_skips_when_no_allowance(MockClient, MockDB):
    """discovery_allowance <= 0 → skip entirely, no API calls made."""
    state = _state(quota_units_spent=15_000, quota_budget=20_000, enrich_quota_reserve=6_000)
    # allowance = 20000 - 15000 - 6000 = -1000
    result = discover_channels(state)
    assert result["current_phase"] == "discovery_skipped"
    assert result["quota_units_spent"] == 0
    assert result["raw_channels"] == []
    MockClient.return_value.search_channels_via_videos.assert_not_called()


@patch("src.nodes.discover_channels.Database")
@patch("src.nodes.discover_channels.YouTubeClient")
def test_skips_when_allowance_exactly_zero(MockClient, MockDB):
    state = _state(quota_units_spent=14_000, quota_budget=20_000, enrich_quota_reserve=6_000)
    # allowance = 0
    result = discover_channels(state)
    assert result["current_phase"] == "discovery_skipped"
    MockClient.return_value.search_channels_via_videos.assert_not_called()


@patch("src.nodes.discover_channels.Database")
@patch("src.nodes.discover_channels.YouTubeClient")
def test_runs_when_allowance_positive(MockClient, MockDB):
    """When allowance > 0, Part A should run."""
    mock_client = MockClient.return_value
    mock_client.search_channels_via_videos.return_value = []
    mock_db = MockDB.return_value
    mock_db._connect.return_value.__enter__ = MagicMock(return_value=MagicMock(
        execute=MagicMock(return_value=MagicMock(fetchall=MagicMock(return_value=[])))
    ))
    mock_db._connect.return_value.__exit__ = MagicMock(return_value=False)

    state = _state(quota_units_spent=5_000, quota_budget=20_000, enrich_quota_reserve=6_000)
    # allowance = 20000 - 5000 - 6000 = 9000
    result = discover_channels(state)
    assert result["current_phase"] == "discovery_complete"
    mock_client.search_channels_via_videos.assert_called()


# ---------------------------------------------------------------------------
# Quota gate: stop Part A early when allowance exhausted mid-loop
# ---------------------------------------------------------------------------

@patch("src.nodes.discover_channels.Database")
@patch("src.nodes.discover_channels.YouTubeClient")
def test_stops_part_a_when_allowance_exhausted(MockClient, MockDB):
    """With 100-unit allowance and 3 keywords, only 1 keyword should be searched."""
    mock_client = MockClient.return_value
    mock_client.search_channels_via_videos.return_value = []
    mock_db = MockDB.return_value
    mock_db._connect.return_value.__enter__ = MagicMock(return_value=MagicMock(
        execute=MagicMock(return_value=MagicMock(fetchall=MagicMock(return_value=[])))
    ))
    mock_db._connect.return_value.__exit__ = MagicMock(return_value=False)

    state = _state(
        search_keywords=["kw1", "kw2", "kw3"],
        quota_units_spent=0,
        quota_budget=20_000,
        enrich_quota_reserve=19_900,  # allowance = 100 → only 1 keyword fits
    )
    result = discover_channels(state)
    assert mock_client.search_channels_via_videos.call_count == 1


@patch("src.nodes.discover_channels.Database")
@patch("src.nodes.discover_channels.YouTubeClient")
def test_runs_all_keywords_when_allowance_sufficient(MockClient, MockDB):
    """When allowance covers all keywords, all should be searched."""
    mock_client = MockClient.return_value
    mock_client.search_channels_via_videos.return_value = []
    mock_db = MockDB.return_value
    mock_db._connect.return_value.__enter__ = MagicMock(return_value=MagicMock(
        execute=MagicMock(return_value=MagicMock(fetchall=MagicMock(return_value=[])))
    ))
    mock_db._connect.return_value.__exit__ = MagicMock(return_value=False)

    state = _state(
        search_keywords=["kw1", "kw2", "kw3"],
        quota_units_spent=0,
        quota_budget=20_000,
        enrich_quota_reserve=0,  # allowance = 20000 → all 3 fit
    )
    result = discover_channels(state)
    assert mock_client.search_channels_via_videos.call_count == 3


# ---------------------------------------------------------------------------
# Default: enrich_quota_reserve=0 preserves old behaviour
# ---------------------------------------------------------------------------

@patch("src.nodes.discover_channels.Database")
@patch("src.nodes.discover_channels.YouTubeClient")
def test_no_reserve_preserves_old_behaviour(MockClient, MockDB):
    """enrich_quota_reserve defaults to 0 — budget gate works as before."""
    mock_client = MockClient.return_value
    mock_client.search_channels_via_videos.return_value = []
    mock_db = MockDB.return_value
    mock_db._connect.return_value.__enter__ = MagicMock(return_value=MagicMock(
        execute=MagicMock(return_value=MagicMock(fetchall=MagicMock(return_value=[])))
    ))
    mock_db._connect.return_value.__exit__ = MagicMock(return_value=False)

    # No enrich_quota_reserve key at all — should default to 0
    state = {
        "search_keywords": ["kw1"],
        "max_results_per_keyword": 20,
        "max_seed_channels": 5,
        "quota_units_spent": 5_000,
        "quota_budget": 20_000,
        # enrich_quota_reserve intentionally omitted
    }
    result = discover_channels(state)
    assert result["current_phase"] == "discovery_complete"
