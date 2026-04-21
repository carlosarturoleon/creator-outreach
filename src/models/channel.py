from typing import Optional
from pydantic import BaseModel, Field


class ChannelData(BaseModel):
    channel_id: str
    channel_title: str
    description: str = ""
    thumbnail_url: str = ""
    subscriber_count: int = 0
    total_view_count: int = 0
    video_count: int = 0
    country: Optional[str] = None
    default_language: Optional[str] = None
    keywords: list[str] = Field(default_factory=list)
    avg_views_per_video: float = 0.0
    avg_likes_per_video: float = 0.0
    avg_comments_per_video: float = 0.0
    engagement_rate: float = 0.0
    upload_frequency_days: float = 0.0
    recent_video_titles: list[str] = Field(default_factory=list)
    search_keyword: str = ""
