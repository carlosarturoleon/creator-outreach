import time

from googleapiclient.errors import HttpError

from src.db.database import Database
from src.logger import get_logger
from src.state import GraphState
from src.tools.youtube_client import YouTubeClient

log = get_logger(__name__)


def discover_channels(state: GraphState) -> dict:
    """
    Supplementary discovery node that finds channels missed by keyword search:

    Part A — Video-level search: calls search(type="video") per keyword and
    extracts unique channel IDs. Surfaces smaller/niche creators that YouTube's
    channel-search doesn't rank.

    Part B — Related-channel traversal: for each high-scoring channel already
    in the DB (composite_score >= 50), fetches related channels via
    relatedToVideoId. Traverses one hop in YouTube's recommendation graph.

    Results are merged into raw_channels (same field as search_channels) via
    operator.add, so deduplication handles overlap naturally.
    """
    keywords = state["search_keywords"]
    max_results = state.get("max_results_per_keyword", 20)
    max_seed_channels = state.get("max_seed_channels", 10)

    client = YouTubeClient()
    db = Database()

    # Track all channel IDs already seen in this node (across both parts)
    seen_ids: set[str] = set()
    new_channels: list[dict] = []

    # ── Part A: Video-level keyword search ────────────────────────────────────
    log.info("discover_channels Part A — video search, %d keywords", len(keywords))
    for i, keyword in enumerate(keywords, 1):
        try:
            results = client.search_channels_via_videos(keyword, max_results=max_results)
            added = 0
            for ch in results:
                cid = ch.get("channel_id")
                if not cid or cid in seen_ids:
                    continue
                seen_ids.add(cid)
                new_channels.append(ch)
                added += 1
                try:
                    db.upsert_channel(ch, touch_last_updated=False)
                except Exception as db_err:
                    log.warning("  DB save failed for %s: %s", cid, db_err)
            log.info("  [%d/%d] video search %r → %d new channels", i, len(keywords), keyword, added)
        except HttpError as e:
            if e.resp.status == 403 and "quota" in str(e).lower():
                log.error("  YouTube quota exhausted during video search — stopping Part A early")
                break
            log.error("  [%d/%d] video search error for %r: %s", i, len(keywords), keyword, e)
        except Exception as e:
            log.error("  [%d/%d] video search error for %r: %s", i, len(keywords), keyword, e)

    log.info("discover_channels Part A DONE — %d channels found via video search", len(new_channels))

    # ── Part B: Related-channel traversal from top-scored channels ────────────
    log.info("discover_channels Part B — related channel traversal (max %d seeds)", max_seed_channels)
    try:
        with db._connect() as conn:
            rows = conn.execute(
                """
                SELECT si.channel_id
                FROM scored_influencers si
                WHERE si.composite_score >= 50
                ORDER BY si.composite_score DESC
                LIMIT ?
                """,
                (max_seed_channels,),
            ).fetchall()
        seed_channel_ids = [row[0] for row in rows]
    except Exception as e:
        log.warning("discover_channels — could not load seed channels from DB: %s", e)
        seed_channel_ids = []

    log.info("  Found %d seed channels for related traversal", len(seed_channel_ids))
    part_b_count = 0
    for seed_id in seed_channel_ids:
        try:
            related_ids = client.get_related_channels(seed_id, max_videos=3, max_related=10)
            added = 0
            for cid in related_ids:
                if cid in seen_ids:
                    continue
                seen_ids.add(cid)
                ch = {
                    "channel_id": cid,
                    "channel_title": "",
                    "description": "",
                    "thumbnail_url": "",
                    "search_keyword": f"related:{seed_id}",
                }
                new_channels.append(ch)
                added += 1
                try:
                    db.upsert_channel(ch, touch_last_updated=False)
                except Exception as db_err:
                    log.warning("  DB save failed for related channel %s: %s", cid, db_err)
            part_b_count += added
            log.info("  Seed %s → %d related channels", seed_id, added)
        except HttpError as e:
            if e.resp.status == 403 and "quota" in str(e).lower():
                log.error("  YouTube quota exhausted during related traversal — stopping Part B early")
                break
            log.error("  Related traversal error for seed %s: %s", seed_id, e)
        except Exception as e:
            log.error("  Related traversal error for seed %s: %s", seed_id, e)

    log.info("discover_channels Part B DONE — %d channels found via related traversal", part_b_count)
    log.info("discover_channels DONE — %d total new channels discovered", len(new_channels))

    return {
        "raw_channels": new_channels,
        "current_phase": "discovery_complete",
    }
