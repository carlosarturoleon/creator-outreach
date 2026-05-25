"""Tests for src/scoring/keyword_scorer.py — pure functions, no mocking needed."""
import pytest
from src.scoring.keyword_scorer import (
    score_channel_relevance,
    _keyword_points,
    _upload_freq_score,
    _view_ratio_score,
    _infer_niche_tags,
)


# ---------------------------------------------------------------------------
# score_channel_relevance — main public function
# ---------------------------------------------------------------------------

def test_high_value_keyword_gives_nonzero_score():
    ch = {"description": "I teach google analytics and GA4 for marketers."}
    result = score_channel_relevance(ch)
    assert result["relevance_score"] > 0
    assert result["keyword_score_raw"] >= 3  # "google analytics" = 3 pts


def test_negative_keyword_disqualifies():
    ch = {"description": "Top crypto signals and NFT reviews every week."}
    result = score_channel_relevance(ch)
    assert result["relevance_score"] == 0.0
    assert "Disqualified" in result["relevance_rationale"]
    assert result["niche_tags"] == []


def test_multiple_high_value_keywords_accumulate():
    ch = {"description": "google analytics and attribution tracking"}
    result = score_channel_relevance(ch)
    # "google analytics" = 3, "attribution" = 3, "analytics" (LOW) = 1 → raw >= 6
    assert result["keyword_score_raw"] >= 6


def test_no_keywords_returns_zero():
    result = score_channel_relevance({})
    assert result["relevance_score"] == 0.0
    assert result["keyword_score_raw"] == 0


def test_empty_description_returns_zero():
    ch = {"description": "", "channel_title": "", "keywords": [], "recent_video_titles": []}
    result = score_channel_relevance(ch)
    assert result["relevance_score"] == 0.0


def test_relevance_score_capped_at_30():
    # Cram every keyword into the description
    ch = {
        "description": (
            "google analytics ga4 attribution conversion tracking marketing analytics "
            "looker studio data studio google tag manager gtm facebook pixel meta pixel "
            "ecommerce analytics shopify analytics multi-channel tracking marketing attribution "
            "analytics dashboard marketing data supermetrics ppc paid advertising "
            "google ads facebook ads meta ads performance marketing roi tracking roi measurement "
            "marketing measurement data-driven marketing digital analytics tiktok ads linkedin ads "
            "ad performance shopify bigquery looker tableau power bi ads manager marketing dashboard "
            "affiliate marketing martech digital marketing marketing tips business growth seo "
            "content marketing email marketing lead generation growth hacking saas analytics data ecommerce"
        )
    }
    result = score_channel_relevance(ch)
    assert result["relevance_score"] <= 30.0


def test_niche_tags_max_5():
    ch = {
        "description": (
            "google analytics ga4 attribution conversion tracking marketing analytics "
            "looker studio gtm facebook pixel shopify analytics"
        )
    }
    result = score_channel_relevance(ch)
    assert len(result["niche_tags"]) <= 5


def test_niche_tags_deduped_looker_studio():
    # "data studio" and "looker studio" both map to "Looker Studio"
    ch = {"description": "learn looker studio and data studio for marketers"}
    result = score_channel_relevance(ch)
    assert result["niche_tags"].count("Looker Studio") == 1


def test_none_description_no_exception():
    ch = {"description": None}
    result = score_channel_relevance(ch)  # should not raise
    assert "relevance_score" in result


def test_none_fields_no_exception():
    ch = {"description": None, "channel_title": None, "keywords": None, "recent_video_titles": None}
    result = score_channel_relevance(ch)
    assert "relevance_score" in result


def test_rationale_mentions_matched_keyword():
    ch = {"description": "google analytics tutorials and GA4 setup guides"}
    result = score_channel_relevance(ch)
    assert "google analytics" in result["relevance_rationale"].lower()


def test_medium_value_keyword_scores_2_pts():
    ch = {"description": "master ppc advertising for your business"}
    result = score_channel_relevance(ch)
    assert result["keyword_score_raw"] == 2  # "ppc" = 2 pts


def test_low_value_keyword_scores_1_pt():
    ch = {"description": "digital marketing strategies for beginners with seo tips"}
    result = score_channel_relevance(ch)
    # "digital marketing" = 1, "seo" = 1 → 2 pts total
    assert result["keyword_score_raw"] == 2


def test_keyword_in_title_also_counts():
    ch = {"channel_title": "Google Analytics Pro", "description": "short"}
    result = score_channel_relevance(ch)
    # "google analytics" found in title text blob
    assert result["keyword_score_raw"] >= 3


def test_keyword_in_recent_video_titles_counts():
    ch = {"recent_video_titles": ["Attribution Models Explained", "GA4 Migration Guide"]}
    result = score_channel_relevance(ch)
    assert result["keyword_score_raw"] >= 3  # "attribution" and "ga4"


# ---------------------------------------------------------------------------
# _upload_freq_score
# ---------------------------------------------------------------------------

def test_upload_freq_weekly():
    assert _upload_freq_score(7.0) == 10.0


def test_upload_freq_biweekly():
    assert _upload_freq_score(14.0) == 5.0


def test_upload_freq_monthly():
    assert _upload_freq_score(30.0) == 2.0


def test_upload_freq_quarterly():
    assert _upload_freq_score(90.0) == 0.0


def test_upload_freq_zero():
    assert _upload_freq_score(0.0) == 0.0


# ---------------------------------------------------------------------------
# _view_ratio_score
# ---------------------------------------------------------------------------

def test_view_ratio_capped_at_10():
    # 100k views / 1k subscribers = 100% ratio → capped at 10
    assert _view_ratio_score(100_000.0, 1_000) == 10.0


def test_view_ratio_proportional():
    # 1000 views / 10000 subs = 10% → 10 pts raw but capped
    score = _view_ratio_score(1_000.0, 10_000)
    assert score == 10.0  # (1000/10000)*100 = 10


def test_view_ratio_low():
    # 100 views / 10000 subs = 1% → 1 pt
    score = _view_ratio_score(100.0, 10_000)
    assert score == pytest.approx(1.0, abs=0.1)


def test_view_ratio_zero_subscribers():
    assert _view_ratio_score(1_000.0, 0) == 0.0


def test_view_ratio_zero_views():
    assert _view_ratio_score(0.0, 10_000) == 0.0


# ---------------------------------------------------------------------------
# _infer_niche_tags
# ---------------------------------------------------------------------------

def test_infer_niche_tags_known_keyword():
    tags = _infer_niche_tags(["google analytics"])
    assert "Google Analytics" in tags


def test_infer_niche_tags_unknown_keyword_skipped():
    tags = _infer_niche_tags(["some unknown keyword"])
    assert tags == []


def test_infer_niche_tags_max_5():
    many = ["google analytics", "ga4", "attribution", "conversion tracking",
            "marketing analytics", "looker studio", "gtm"]
    tags = _infer_niche_tags(many)
    assert len(tags) <= 5
