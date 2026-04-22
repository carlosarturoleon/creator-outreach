import time

from googleapiclient.errors import HttpError

from src.logger import get_logger
from src.state import GraphState
from src.tools.youtube_client import YouTubeClient
from src.db.database import Database

log = get_logger(__name__)


def _fmt_seconds(secs: float) -> str:
    """Format a duration in seconds as 'Xm Ys' or 'Xs'."""
    secs = int(secs)
    if secs >= 60:
        return f"{secs // 60}m {secs % 60}s"
    return f"{secs}s"


def search_channels(state: GraphState) -> dict:
    """
    Node 1: Search YouTube for channels matching each keyword.
    Deduplicates by channel_id across keywords.

    Each channel is saved to the DB immediately after discovery so that
    search results survive a mid-run crash. On retry the dedup and
    enrichment cache will skip channels already processed.

    Logs real-time progress with completed/pending counts and ETA.
    """
    keywords = state["search_keywords"]
    total = len(keywords)
    log.info("search_channels START — %d keywords, max %d results each",
             total, state["max_results_per_keyword"])

    client = YouTubeClient()
    db = Database()
    seen_ids: set[str] = set()
    new_channels: list[dict] = []
    loop_start = time.monotonic()

    already_searched = db.get_searched_keywords()
    keywords_set = set(keywords)
    if already_searched and keywords_set.issubset(already_searched):
        # All keywords were completed in a previous run — start fresh
        log.info("  Previous run was complete — clearing search cache for a fresh run")
        db.clear_searched_keywords()
        already_searched = set()
    elif already_searched:
        pending = keywords_set - already_searched
        log.info("  Resuming — %d/%d keywords already searched, %d pending",
                 len(already_searched & keywords_set), total, len(pending))

    for i, keyword in enumerate(keywords, 1):
        if keyword in already_searched:
            cached = db.get_channels_by_keyword(keyword)
            for ch in cached:
                cid = ch.get("channel_id")
                if cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    new_channels.append(ch)
            log.info("  [%d/%d] Skipping (already searched): %r — loaded %d channels from DB",
                     i, total, keyword, len(cached))
            continue
        try:
            log.info("  [%d/%d] Searching: %r", i, total, keyword)
            results = client.search_channels(
                keyword=keyword,
                max_results=state["max_results_per_keyword"],
            )
            added = 0
            db_errors = 0
            for ch in results:
                cid = ch.get("channel_id")
                if cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    new_channels.append(ch)
                    added += 1
                    # Save immediately — search results survive crashes this way.
                    # touch_last_updated=False preserves any existing enrichment
                    # timestamp so the enrichment cache is not incorrectly reset.
                    try:
                        db.upsert_channel(ch, touch_last_updated=False)
                    except Exception as db_err:
                        db_errors += 1
                        log.warning("  DB save failed for %s: %s", cid, db_err)

            log.info("  [%d/%d] %r → %d new channels saved to DB (total: %d)%s",
                     i, total, keyword, added, len(new_channels),
                     f" [{db_errors} DB errors]" if db_errors else "")
            db.mark_keyword_searched(keyword, added)

            # Progress + ETA
            elapsed = time.monotonic() - loop_start
            avg_per_kw = elapsed / i
            remaining = total - i
            if remaining > 0:
                eta = _fmt_seconds(avg_per_kw * remaining)
                progress_msg = (
                    f"  Progress: {i}/{total} done, {remaining} pending"
                    f" | avg {avg_per_kw:.1f}s/kw | ETA ~{eta}"
                )
                log.info(progress_msg)
            else:
                log.info("  Search complete in %s", _fmt_seconds(elapsed))

        except HttpError as e:
            if e.resp.status == 403 and (
                "quotaExceeded" in str(e) or "rateLimitExceeded" in str(e) or "quota" in str(e).lower()
            ):
                log.error("  YouTube quota exhausted — stopping search early (%d/%d keywords done)", i - 1, total)
                break
            log.error("  [%d/%d] Error for keyword %r: %s", i, total, keyword, e)
        except Exception as e:
            log.error("  [%d/%d] Error for keyword %r: %s", i, total, keyword, e)

    log.info("search_channels DONE — %d unique channels found", len(new_channels))
    return {
        "raw_channels": new_channels,
        "current_phase": "search_complete",
    }
