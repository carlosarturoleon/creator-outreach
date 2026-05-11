import math
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


def enrich_pending_stubs(state: GraphState) -> dict:
    """
    Node 0: Enrich stub channels left over from previous quota-cut runs.

    Loads channels from the DB with subscriber_count = 0 (not yet enriched),
    enriches them via the YouTube API, and emits them into enriched_channels.
    This runs before search_channels so that leftover work is prioritized and
    quota is not re-spent searching for channels we already know about.

    Channels that were fully emailed are skipped (same logic as deduplicate_vs_db).
    """
    db = Database()
    stubs = db.get_stub_channels()

    if not stubs:
        log.info("enrich_pending_stubs — no pending stubs in DB, skipping")
        return {"enriched_channels": [], "error_log": [], "quota_units_spent": 0, "current_phase": "no_stubs"}

    # Skip channels already emailed
    emailed_ids = db.get_emailed_channel_ids()
    stubs = [ch for ch in stubs if ch.get("channel_id") not in emailed_ids]

    if not stubs:
        log.info("enrich_pending_stubs — all stubs already emailed, skipping")
        return {"enriched_channels": [], "error_log": [], "quota_units_spent": 0, "current_phase": "no_stubs"}

    log.info("enrich_pending_stubs START — %d pending stubs to enrich", len(stubs))

    errors: list[str] = []
    enriched: list[dict] = []
    quota_units = 0

    client = YouTubeClient()

    # Phase A: batch stats (~1 unit per 50 channels)
    stale_ids = [ch["channel_id"] for ch in stubs]
    log.info("  Phase A: fetching batch stats for %d stub channels", len(stale_ids))
    try:
        stats_list = client.get_channel_stats(stale_ids)
        quota_units += math.ceil(len(stale_ids) / 50)
        log.info("  Phase A: received stats for %d channels", len(stats_list))
    except QuotaExhaustedError as e:
        log.error("  Phase A: quota exhausted — cannot enrich stubs: %s", e)
        errors.append(f"[enrich_pending_stubs] Quota exhausted in Phase A: {e}")
        return {"enriched_channels": [], "error_log": errors, "quota_units_spent": quota_units, "current_phase": "no_stubs"}
    except Exception as e:
        log.error("  Phase A: batch stats failed: %s", e)
        errors.append(f"[enrich_pending_stubs] Batch stats failed: {e}")
        stats_list = []

    stats_map: dict[str, dict] = {s["channel_id"]: s for s in stats_list}

    # Phase B: per-channel video engagement (~3 units each)
    quota_exhausted = False
    for i, ch in enumerate(stubs, 1):
        cid = ch["channel_id"]
        title = ch.get("channel_title", cid)
        stats = stats_map.get(cid, {})
        log.info("  Phase B [%d/%d] %s (%s)", i, len(stubs), title, cid)

        if quota_exhausted:
            video_stats = client._empty_video_stats()
        else:
            try:
                video_stats = client.get_channel_video_stats(
                    channel_id=cid,
                    max_videos=settings.max_videos_to_sample,
                )
                quota_units += 3
            except QuotaExhaustedError as e:
                log.warning("  Phase B: quota exhausted at channel %d/%d — skipping remaining", i, len(stubs))
                errors.append(f"[enrich_pending_stubs] Quota exhausted at Phase B channel {i}/{len(stubs)}: {e}")
                quota_exhausted = True
                video_stats = client._empty_video_stats()
            except Exception as e:
                log.warning("    Video stats failed for %s: %s", cid, e)
                errors.append(f"[enrich_pending_stubs] Video stats failed for {cid}: {e}")
                video_stats = client._empty_video_stats()

        enriched_ch = {**ch, **stats, **video_stats}

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
            errors.append(f"[enrich_pending_stubs] DB upsert failed for {cid}: {e}")

    log.info("enrich_pending_stubs DONE — %d enriched, ~%d quota units, %d errors",
             len(enriched), quota_units, len(errors))
    return {
        "enriched_channels": enriched,
        "error_log": errors,
        "quota_units_spent": quota_units,
        "current_phase": "stubs_enriched",
    }
