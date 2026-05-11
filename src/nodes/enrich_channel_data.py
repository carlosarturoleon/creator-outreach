import re

from src.logger import get_logger
from src.state import GraphState
from src.tools.youtube_client import YouTubeClient, QuotaExhaustedError
from src.db.database import Database
from src.config import settings

log = get_logger(__name__)

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def _extract_email(text: str) -> str | None:
    m = _EMAIL_RE.search(text or "")
    return m.group(0) if m else None


def enrich_channel_data(state: GraphState) -> dict:
    """
    Node 2: Enrich channels with stats and engagement metrics.

    Phase A: Batched channels.list → subscriber count, views, language, keywords.
    Phase B: Per-channel video stats via playlistItems path (quota-efficient).

    Channels previously enriched (subscriber_count > 0 in DB) are served from
    the local SQLite cache permanently — no re-fetch unless --force-reenrich is set.

    Persists each enriched channel to SQLite.
    """
    channels = state.get("pre_filtered_channels", state.get("deduped_channels", []))
    if not channels:
        log.info("enrich_channel_data — no channels to enrich, skipping")
        return {"enriched_channels": [], "error_log": [], "quota_units_spent": 0, "current_phase": "enrichment_complete"}

    force_reenrich = state.get("force_reenrich", False)
    log.info("enrich_channel_data START — %d channels to enrich%s",
             len(channels), " (force-reenrich: cache bypassed)" if force_reenrich else "")
    db = Database()
    errors: list[str] = []

    # --- Cache lookup: skip channels already enriched unless force_reenrich ---
    all_ids = [ch["channel_id"] for ch in channels]
    cached_map = {} if force_reenrich else db.get_cached_channels(all_ids)

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
    quota_units = 0

    if stale:
        client = YouTubeClient()

        # Phase A: batch stats for stale channels only (~1 unit per 50 channels)
        stale_ids = [ch["channel_id"] for ch in stale]
        log.info("  Phase A: fetching batch stats for %d channels", len(stale_ids))
        try:
            stats_list = client.get_channel_stats(stale_ids)
            import math
            quota_units += math.ceil(len(stale_ids) / 50)
            log.info("  Phase A: received stats for %d channels", len(stats_list))
        except QuotaExhaustedError as e:
            log.error("  Phase A: quota exhausted — will still attempt per-channel video stats: %s", e)
            errors.append(f"[enrich] Quota exhausted in Phase A: {e}")
            stats_list = []
        except Exception as e:
            log.error("  Phase A: batch stats failed: %s", e)
            errors.append(f"[enrich] Batch stats failed: {e}")
            stats_list = []

        stats_map: dict[str, dict] = {s["channel_id"]: s for s in stats_list}

        # Phase B: per-channel video engagement for stale channels only (~3 units each)
        quota_exhausted = False
        for i, ch in enumerate(stale, 1):
            cid = ch["channel_id"]
            title = ch.get("channel_title", cid)
            stats = stats_map.get(cid, {})
            log.info("  Phase B [%d/%d] %s (%s)", i, len(stale), title, cid)

            if quota_exhausted:
                video_stats = client._empty_video_stats()
            else:
                try:
                    video_stats = client.get_channel_video_stats(
                        channel_id=cid,
                        max_videos=settings.max_videos_to_sample,
                    )
                    quota_units += 3
                    log.debug("    engagement=%.2f%%, subscribers=%s",
                              video_stats.get("engagement_rate", 0),
                              stats.get("subscriber_count", "?"))
                except QuotaExhaustedError as e:
                    log.warning("  Phase B: quota exhausted at channel %d/%d — skipping remaining video stats", i, len(stale))
                    errors.append(f"[enrich] Quota exhausted at Phase B channel {i}/{len(stale)}: {e}")
                    quota_exhausted = True
                    video_stats = client._empty_video_stats()
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

    log.info("enrich_channel_data DONE — %d enriched (%d from cache, %d from API), ~%d quota units, %d errors",
             len(enriched), len(fresh), len(stale), quota_units, len(errors))
    return {
        "enriched_channels": enriched,
        "error_log": errors,
        "quota_units_spent": quota_units,
        "current_phase": "enrichment_complete",
    }
