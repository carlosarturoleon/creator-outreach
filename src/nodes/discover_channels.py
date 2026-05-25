import time
from pathlib import Path

import yaml
from googleapiclient.errors import HttpError

from src.db.database import Database
from src.logger import get_logger
from src.state import GraphState
from src.tools.youtube_client import QuotaExhaustedError, YouTubeClient

log = get_logger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent.parent / "pipeline_config.yaml"
with open(_CONFIG_PATH) as _f:
    _disc_cfg = yaml.safe_load(_f)["discovery"]

_SEED_SCORE_THRESHOLD: int = _disc_cfg["seed_channel_score_threshold"]
_RELATED_MAX_VIDEOS: int = _disc_cfg["related_max_videos"]
_RELATED_MAX_RESULTS: int = _disc_cfg["related_max_results"]
_QUOTA_COST_VIDEO_SEARCH: int = _disc_cfg["quota_cost_video_search"]
_QUOTA_COST_RELATED: int = _disc_cfg["quota_cost_related_traversal"]


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

    # Quota budget gate — skip discovery if we're already over budget or no room after reserve
    quota_spent = state.get("quota_units_spent", 0)
    quota_budget = state.get("quota_budget", 8000)
    enrich_reserve = state.get("enrich_quota_reserve", 0)
    # Units available for discovery = budget minus already spent minus what must be kept for enrichment
    discovery_allowance = quota_budget - quota_spent - enrich_reserve
    if discovery_allowance <= 0:
        log.warning(
            "discover_channels — no quota left for discovery after reserving %d units for enrichment "
            "(%d/%d spent), skipping",
            enrich_reserve, quota_spent, quota_budget,
        )
        return {"raw_channels": [], "quota_units_spent": 0, "current_phase": "discovery_skipped"}

    log.info(
        "discover_channels — quota budget: %d/%d used, %d reserved for enrichment, %d available for discovery",
        quota_spent, quota_budget, enrich_reserve, discovery_allowance,
    )

    client = YouTubeClient()
    db = Database()

    # Track all channel IDs already seen in this node (across both parts)
    seen_ids: set[str] = set()
    new_channels: list[dict] = []
    quota_units = 0

    # ── Part A: Video-level keyword search ────────────────────────────────────
    log.info("discover_channels Part A — video search, %d keywords", len(keywords))
    for i, keyword in enumerate(keywords, 1):
        # Stop early if spending another 100 units would eat into the enrichment reserve
        if quota_units >= discovery_allowance:
            log.warning(
                "  [%d/%d] discovery allowance reached (%d/%d units used) — stopping Part A to preserve enrichment quota",
                i, len(keywords), quota_units, discovery_allowance,
            )
            break
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
            quota_units += _QUOTA_COST_VIDEO_SEARCH
            log.info("  [%d/%d] video search %r → %d new channels", i, len(keywords), keyword, added)
        except QuotaExhaustedError:
            log.error("  YouTube quota exhausted — stopping Part A early")
            break
        except HttpError as e:
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
                WHERE si.composite_score >= ?
                ORDER BY si.composite_score DESC
                LIMIT ?
                """,
                (_SEED_SCORE_THRESHOLD, max_seed_channels),
            ).fetchall()
        seed_channel_ids = [row[0] for row in rows]
    except Exception as e:
        log.warning("discover_channels — could not load seed channels from DB: %s", e)
        seed_channel_ids = []

    log.info("  Found %d seed channels for related traversal", len(seed_channel_ids))
    part_b_count = 0
    for seed_id in seed_channel_ids:
        try:
            related_ids = client.get_related_channels(seed_id, max_videos=_RELATED_MAX_VIDEOS, max_related=_RELATED_MAX_RESULTS)
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
            quota_units += _QUOTA_COST_RELATED  # ~1 (channels.list) + 1 (playlistItems) + 100×max_videos (search per video)
            log.info("  Seed %s → %d related channels", seed_id, added)
        except QuotaExhaustedError:
            log.error("  YouTube quota exhausted — stopping Part B early")
            break
        except HttpError as e:
            log.error("  Related traversal error for seed %s: %s", seed_id, e)
        except Exception as e:
            log.error("  Related traversal error for seed %s: %s", seed_id, e)

    log.info("discover_channels Part B DONE — %d channels found via related traversal", part_b_count)
    log.info("discover_channels DONE — %d total new channels discovered, ~%d quota units spent", len(new_channels), quota_units)

    return {
        "raw_channels": new_channels,
        "quota_units_spent": quota_units,
        "current_phase": "discovery_complete",
    }
