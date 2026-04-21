from src.state import GraphState
from src.tools.youtube_client import YouTubeClient
from src.db.database import Database
from src.config import settings


def enrich_channel_data(state: GraphState) -> dict:
    """
    Node 2: Enrich channels with stats and engagement metrics.

    Phase A: Batched channels.list → subscriber count, views, language, keywords.
    Phase B: Per-channel video stats via playlistItems path (quota-efficient).

    Persists each enriched channel to SQLite.
    """
    channels = state.get("pre_filtered_channels", state.get("deduped_channels", []))
    if not channels:
        return {"enriched_channels": [], "error_log": [], "current_phase": "enrichment_complete"}

    client = YouTubeClient()
    db = Database()
    errors: list[str] = []

    # Phase A: batch stats
    channel_ids = [ch["channel_id"] for ch in channels]
    try:
        stats_list = client.get_channel_stats(channel_ids)
    except Exception as e:
        errors.append(f"[enrich] Batch stats failed: {e}")
        stats_list = []

    stats_map: dict[str, dict] = {s["channel_id"]: s for s in stats_list}

    # Phase B: per-channel video engagement
    enriched: list[dict] = []
    for ch in channels:
        cid = ch["channel_id"]
        stats = stats_map.get(cid, {})

        try:
            video_stats = client.get_channel_video_stats(
                channel_id=cid,
                max_videos=settings.max_videos_to_sample,
            )
        except Exception as e:
            errors.append(f"[enrich] Video stats failed for {cid}: {e}")
            video_stats = client._empty_video_stats()

        enriched_ch = {**ch, **stats, **video_stats}
        enriched.append(enriched_ch)

        try:
            db.upsert_channel(enriched_ch)
        except Exception as e:
            errors.append(f"[enrich] DB upsert failed for {cid}: {e}")

    print(f"[enrich_channel_data] Enriched {len(enriched)} channels ({len(errors)} errors)")
    return {
        "enriched_channels": enriched,
        "error_log": errors,
        "current_phase": "enrichment_complete",
    }
