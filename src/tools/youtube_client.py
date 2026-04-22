import time
from datetime import datetime

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.config import settings
from src.logger import get_logger

log = get_logger(__name__)
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _is_quota_error(e: HttpError) -> bool:
    if e.resp.status != 403:
        return False
    reason = ""
    if e.error_details:
        reason = e.error_details[0].get("reason", "")
    return reason in ("quotaExceeded", "rateLimitExceeded") or "quota" in str(e).lower()


def _execute_with_backoff(request, max_retries: int = 5):
    """Execute a YouTube API request with exponential backoff on quota/server errors."""
    delay = 1.0
    for attempt in range(max_retries):
        try:
            return request.execute()
        except HttpError as e:
            status = e.resp.status
            if status == 403:
                if _is_quota_error(e):
                    if attempt < max_retries - 1:
                        log.warning("YouTube quota exceeded, retrying in %.1fs (attempt %d/%d)",
                                    delay, attempt + 1, max_retries)
                        time.sleep(delay)
                        delay = min(delay * 2, 60.0)
                        continue
                raise  # Non-quota 403 (auth error) — don't retry
            if status in _RETRYABLE_STATUS_CODES:
                if attempt < max_retries - 1:
                    log.warning("YouTube HTTP %d, retrying in %.1fs (attempt %d/%d)",
                                status, delay, attempt + 1, max_retries)
                    time.sleep(delay)
                    delay = min(delay * 2, 60.0)
                    continue
            raise
    raise RuntimeError(f"YouTube API request failed after {max_retries} retries")


class YouTubeClient:
    def __init__(self):
        self._keys = list(settings.youtube_api_keys)
        self._key_index = 0
        self.service = self._build_service()

    def _build_service(self):
        key = self._keys[self._key_index]
        log.debug("YouTubeClient using API key index %d", self._key_index)
        return build("youtube", "v3", developerKey=key)

    def _rotate_key(self) -> bool:
        """Switch to the next API key. Returns True if a new key is available."""
        if self._key_index + 1 >= len(self._keys):
            return False
        self._key_index += 1
        log.warning("YouTube quota exhausted — rotating to API key %d/%d",
                    self._key_index + 1, len(self._keys))
        self.service = self._build_service()
        return True

    def _execute(self, request_fn):
        """Build and execute a request, rotating API key on quota exhaustion if possible."""
        while True:
            try:
                return _execute_with_backoff(request_fn(self.service))
            except HttpError as e:
                if _is_quota_error(e) and self._rotate_key():
                    continue  # retry with new key
                raise

    def search_channels(self, keyword: str, max_results: int = 20) -> list[dict]:
        """
        Search YouTube channels by keyword.
        Quota cost: 100 units per call.
        """
        response = self._execute(lambda svc: svc.search().list(
            q=keyword,
            type="channel",
            part="snippet",
            maxResults=max_results,
            relevanceLanguage="en",
        ))

        channels = []
        for item in response.get("items", []):
            snippet = item.get("snippet", {})
            channels.append({
                "channel_id": snippet.get("channelId"),
                "channel_title": snippet.get("title", ""),
                "description": snippet.get("description", ""),
                "thumbnail_url": snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
                "search_keyword": keyword,
            })
        return channels

    def get_channel_stats(self, channel_ids: list[str]) -> list[dict]:
        """
        Batch fetch channel stats (subscriber count, views, etc.).
        Quota cost: 1 unit per call (up to 50 IDs per call).
        """
        results = []
        for i in range(0, len(channel_ids), 50):
            batch = channel_ids[i : i + 50]
            batch_ids = ",".join(batch)
            response = self._execute(lambda svc, ids=batch_ids: svc.channels().list(
                id=ids,
                part="snippet,statistics,brandingSettings",
            ))

            for item in response.get("items", []):
                stats = item.get("statistics", {})
                branding = item.get("brandingSettings", {}).get("channel", {})
                snippet = item.get("snippet", {})
                keywords_raw = branding.get("keywords", "")
                results.append({
                    "channel_id": item["id"],
                    "subscriber_count": int(stats.get("subscriberCount", 0)),
                    "total_view_count": int(stats.get("viewCount", 0)),
                    "video_count": int(stats.get("videoCount", 0)),
                    "country": snippet.get("country"),
                    "default_language": snippet.get("defaultLanguage"),
                    "description": snippet.get("description", ""),
                    "keywords": keywords_raw.split() if keywords_raw else [],
                })
        return results

    def get_channel_video_stats(self, channel_id: str, max_videos: int = 10) -> dict:
        """
        Fetch engagement metrics for a channel's most recent videos.

        Uses the quota-efficient path:
          channels.list (contentDetails) → playlistItems.list → videos.list
        Total cost: ~3 units (vs 100+ units via search.list).
        """
        # Step 1: get uploads playlist ID (1 unit)
        ch_response = self._execute(lambda svc: svc.channels().list(
            id=channel_id,
            part="contentDetails",
        ))

        items = ch_response.get("items", [])
        if not items:
            return self._empty_video_stats()

        uploads_playlist_id = (
            items[0]["contentDetails"]["relatedPlaylists"].get("uploads")
        )
        if not uploads_playlist_id:
            return self._empty_video_stats()

        # Step 2: get recent video IDs from uploads playlist (1 unit)
        playlist_response = self._execute(lambda svc: svc.playlistItems().list(
            playlistId=uploads_playlist_id,
            part="snippet,contentDetails",
            maxResults=max_videos,
        ))

        playlist_items = playlist_response.get("items", [])
        if not playlist_items:
            return self._empty_video_stats()

        video_ids = [item["contentDetails"]["videoId"] for item in playlist_items]
        video_titles = [item["snippet"]["title"] for item in playlist_items]
        publish_dates = [item["snippet"]["publishedAt"] for item in playlist_items]

        # Step 3: get statistics for all video IDs in one call (1 unit)
        videos_response = self._execute(lambda svc: svc.videos().list(
            id=",".join(video_ids),
            part="statistics",
        ))

        views, likes, comments = [], [], []
        for item in videos_response.get("items", []):
            s = item.get("statistics", {})
            views.append(int(s.get("viewCount", 0)))
            likes.append(int(s.get("likeCount", 0)))
            comments.append(int(s.get("commentCount", 0)))

        if not views:
            return self._empty_video_stats()

        avg_views = sum(views) / len(views)
        avg_likes = sum(likes) / len(likes)
        avg_comments = sum(comments) / len(comments)
        engagement_rate = (
            (avg_likes + avg_comments) / avg_views * 100 if avg_views > 0 else 0.0
        )

        return {
            "avg_views_per_video": round(avg_views, 2),
            "avg_likes_per_video": round(avg_likes, 2),
            "avg_comments_per_video": round(avg_comments, 2),
            "engagement_rate": round(engagement_rate, 4),
            "upload_frequency_days": self._compute_upload_frequency(publish_dates),
            "recent_video_titles": video_titles[:5],
        }

    def _compute_upload_frequency(self, dates: list[str]) -> float:
        """Compute mean days between uploads from ISO8601 date strings."""
        if len(dates) < 2:
            return 0.0
        parsed = sorted(
            [datetime.fromisoformat(d.replace("Z", "+00:00")) for d in dates],
            reverse=True,
        )
        deltas = [(parsed[i] - parsed[i + 1]).days for i in range(len(parsed) - 1)]
        return round(sum(deltas) / len(deltas), 1)

    def _empty_video_stats(self) -> dict:
        return {
            "avg_views_per_video": 0.0,
            "avg_likes_per_video": 0.0,
            "avg_comments_per_video": 0.0,
            "engagement_rate": 0.0,
            "upload_frequency_days": 0.0,
            "recent_video_titles": [],
        }
