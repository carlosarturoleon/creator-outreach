import re

from src.logger import get_logger
from src.state import GraphState
from src.tools.youtube_client import YouTubeClient
from src.db.database import Database
from src.config import settings

log = get_logger(__name__)

_ENRICH_CACHE_DAYS = 7  # reuse DB stats if updated within this many days
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def _extract_email(text: str) -> str | None:
    m = _EMAIL_RE.search(text or "")
    return m.group(0) if m else None


def enrich_channel_data(state: GraphState) -> dict:
    """
    Node 2: Enrich channels with stats and engagement metrics.

    Phase A: Batched channels.list → subscriber count, views, language, keywords.
    Phase B: Per-channel video stats via playlistItems path (quota-efficient).

    Channels enriched within the last _ENRICH_CACHE_DAYS days are served from
    the local SQLite cache — no YouTube API quota consumed for those.

    Persists each enriched channel to SQLite.
    """
    channels = state.get("pre_filtered_channels", state.get("deduped_channels", []))
    if not channels:
        log.info("enrich_channel_data — no channels to enrich, skipping")
        return {"enriched_channels": [], "error_log": [], "current_phase": "enrichment_complete"}

    log.info("enrich_channel_data START — %d channels to enrich", len(channels))
    db = Database()
    errors: list[str] = []

    # --- Cache lookup: which channels have fresh data already? ---
    all_ids = [ch["channel_id"] for ch in channels]
    cached_map = db.get_cached_channels(all_ids, max_age_days=_ENRICH_CACHE_DAYS)

    fresh: list[dict] = []   # served from DB cache
    stale: list[dict] = []   # need YouTube API call
    for ch in channels:
        if ch["channel_id"] in cached_map:
            # Merge search_keyword from the current search run onto the cached row
            cached_ch = {**cached_map[ch["channel_id"]], "search_keyword": ch.get("search_keyword")}
            # Extract email from description if not already set
            if not cached_ch.get("contact_email"):
                extracted = _extract_email(cached_ch.get("description", ""))
                if extracted:
                    cached_ch["contact_email"] = extracted
                    try:
                        db.upsert_channel(cached_ch)
                    except Exception as e:
                        log.warning("  Email upsert failed for %s: %s", ch["channel_id"], e)
                    log.info("  Extracted email for cached %s: %s", cached_ch.get("channel_title"), extracted)
            fresh.append(cached_ch)
        else:
            stale.append(ch)

    log.info("  Cache check — %d fresh (DB cache), %d stale (need API)", len(fresh), len(stale))

    enriched: list[dict] = list(fresh)  # start with cached results

    if stale:
        client = YouTubeClient()

        # Phase A: batch stats for stale channels only
        stale_ids = [ch["channel_id"] for ch in stale]
        log.info("  Phase A: fetching batch stats for %d channels", len(stale_ids))
        try:
            stats_list = client.get_channel_stats(stale_ids)
            log.info("  Phase A: received stats for %d channels", len(stats_list))
        except Exception as e:
            log.error("  Phase A: batch stats failed: %s", e)
            errors.append(f"[enrich] Batch stats failed: {e}")
            stats_list = []

        stats_map: dict[str, dict] = {s["channel_id"]: s for s in stats_list}

        # Phase B: per-channel video engagement for stale channels only
        for i, ch in enumerate(stale, 1):
            cid = ch["channel_id"]
            title = ch.get("channel_title", cid)
            stats = stats_map.get(cid, {})
            log.info("  Phase B [%d/%d] %s (%s)", i, len(stale), title, cid)

            try:
                video_stats = client.get_channel_video_stats(
                    channel_id=cid,
                    max_videos=settings.max_videos_to_sample,
                )
                log.debug("    engagement=%.2f%%, subscribers=%s",
                          video_stats.get("engagement_rate", 0),
                          stats.get("subscriber_count", "?"))
            except Exception as e:
                log.warning("    Video stats failed for %s: %s", cid, e)
                errors.append(f"[enrich] Video stats failed for {cid}: {e}")
                video_stats = client._empty_video_stats()

            enriched_ch = {**ch, **stats, **video_stats}

            # Extract contact email from description if not already set
            if not enriched_ch.get("contact_email"):
                extracted = _extract_email(enriched_ch.get("description", ""))
                if extracted:
                    enriched_ch["contact_email"] = extracted
                    log.info("    Extracted email for %s: %s", title, extracted)

            enriched.append(enriched_ch)

            try:
                db.upsert_channel(enriched_ch)
            except Exception as e:
                log.error("    DB upsert failed for %s: %s", cid, e)
                errors.append(f"[enrich] DB upsert failed for {cid}: {e}")

    log.info("enrich_channel_data DONE — %d enriched (%d from cache, %d from API), %d errors",
             len(enriched), len(fresh), len(stale), len(errors))
    return {
        "enriched_channels": enriched,
        "error_log": errors,
        "current_phase": "enrichment_complete",
    }
