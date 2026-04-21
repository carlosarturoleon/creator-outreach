import pytest


@pytest.fixture
def base_channel():
    """Fully enriched channel that passes all filters."""
    return {
        "channel_id": "UC_test_001",
        "channel_title": "Google Analytics Tutorials",
        "description": "Learn Google Analytics, GA4, and conversion tracking for digital marketers.",
        "subscriber_count": 25_000,
        "engagement_rate": 3.5,
        "avg_views_per_video": 5_000.0,
        "avg_likes_per_video": 200.0,
        "avg_comments_per_video": 50.0,
        "upload_frequency_days": 7.0,
        "keywords": ["google analytics", "ga4", "marketing"],
        "recent_video_titles": ["GA4 Setup Guide", "Attribution Models Explained"],
        "default_language": "en",
        "search_keyword": "google analytics",
        "total_view_count": 500_000,
        "video_count": 100,
        "country": "US",
    }


@pytest.fixture
def negative_channel():
    """Channel that matches negative keywords and should be dropped."""
    return {
        "channel_id": "UC_test_neg",
        "channel_title": "Crypto Gaming Lifestyle",
        "description": "Best crypto gaming tips and lifestyle vlogs for enthusiasts.",
        "subscriber_count": 50_000,
        "engagement_rate": 4.0,
        "avg_views_per_video": 10_000.0,
        "avg_likes_per_video": 400.0,
        "avg_comments_per_video": 100.0,
        "upload_frequency_days": 3.0,
        "keywords": [],
        "recent_video_titles": ["Top 10 Crypto Coins", "Gaming Setup Tour"],
        "default_language": "en",
        "search_keyword": "gaming",
        "total_view_count": 1_000_000,
        "video_count": 200,
        "country": "US",
    }
