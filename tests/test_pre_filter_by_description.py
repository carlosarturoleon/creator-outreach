"""Tests for src/nodes/pre_filter_by_description.py"""
import pytest
from src.nodes.pre_filter_by_description import pre_filter_by_description


def _state(channels):
    return {"deduped_channels": channels}


def _ch(channel_id="UC1", title="", description=""):
    return {"channel_id": channel_id, "channel_title": title, "description": description}


# ---------------------------------------------------------------------------
# Keeps
# ---------------------------------------------------------------------------

def test_keeps_channel_with_niche_keyword_in_description():
    ch = _ch(description="Learn google analytics and GA4 for digital marketers every week.")
    result = pre_filter_by_description(_state([ch]))
    assert len(result["pre_filtered_channels"]) == 1


def test_keeps_channel_with_short_description_failopen():
    # < 30 chars, no keywords → fail-open, keep it
    ch = _ch(description="Marketing tips")
    result = pre_filter_by_description(_state([ch]))
    assert len(result["pre_filtered_channels"]) == 1


def test_keeps_channel_with_empty_description():
    ch = _ch(description="")
    result = pre_filter_by_description(_state([ch]))
    assert len(result["pre_filtered_channels"]) == 1


def test_keeps_channel_with_none_description():
    ch = {"channel_id": "UC1", "channel_title": "Test", "description": None}
    result = pre_filter_by_description(_state([ch]))
    assert len(result["pre_filtered_channels"]) == 1


# ---------------------------------------------------------------------------
# Drops — negative keywords
# ---------------------------------------------------------------------------

def test_drops_channel_with_crypto_in_description():
    ch = _ch(description="Daily crypto signals and NFT investment strategies for beginners.")
    result = pre_filter_by_description(_state([ch]))
    assert len(result["pre_filtered_channels"]) == 0


def test_drops_channel_with_gaming_in_title():
    ch = _ch(title="Gaming Pro Channel", description="We do marketing analytics and data.")
    result = pre_filter_by_description(_state([ch]))
    assert len(result["pre_filtered_channels"]) == 0


def test_drops_channel_with_lifestyle_in_title():
    ch = _ch(title="Lifestyle Vlog Daily", description="google analytics tutorials here")
    result = pre_filter_by_description(_state([ch]))
    assert len(result["pre_filtered_channels"]) == 0


def test_negative_overrides_short_description_failopen():
    # Short desc (< 30 chars) but has negative keyword → still dropped
    ch = _ch(description="crypto tips")
    result = pre_filter_by_description(_state([ch]))
    assert len(result["pre_filtered_channels"]) == 0


# ---------------------------------------------------------------------------
# Drops — no niche keyword in long description
# ---------------------------------------------------------------------------

def test_drops_long_description_with_no_niche_keywords():
    ch = _ch(description="I share my personal journey through cooking, travel, and lifestyle choices every week on YouTube.")
    result = pre_filter_by_description(_state([ch]))
    assert len(result["pre_filtered_channels"]) == 0


# ---------------------------------------------------------------------------
# Mixed batch
# ---------------------------------------------------------------------------

def test_mixed_batch_correctly_partitioned():
    good = _ch("UC_good", description="Google analytics and attribution tutorials for marketers.")
    bad_neg = _ch("UC_neg", description="Best gaming and esports highlights from tournaments worldwide.")
    bad_niche = _ch("UC_niche", description="Cooking and baking recipes for the whole family to enjoy.")
    short = _ch("UC_short", description="hi")

    result = pre_filter_by_description(_state([good, bad_neg, bad_niche, short]))
    ids = [ch["channel_id"] for ch in result["pre_filtered_channels"]]
    assert "UC_good" in ids
    assert "UC_short" in ids
    assert "UC_neg" not in ids
    assert "UC_niche" not in ids


# ---------------------------------------------------------------------------
# Empty input / return shape
# ---------------------------------------------------------------------------

def test_empty_input_returns_empty():
    result = pre_filter_by_description(_state([]))
    assert result["pre_filtered_channels"] == []


def test_return_keys_present():
    result = pre_filter_by_description(_state([]))
    assert "pre_filtered_channels" in result
    assert "current_phase" in result


def test_current_phase_value():
    result = pre_filter_by_description(_state([]))
    assert result["current_phase"] == "pre_filter_complete"
