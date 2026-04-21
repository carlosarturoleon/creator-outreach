from src.logger import get_logger
from src.state import GraphState
from src.db.database import Database

log = get_logger(__name__)


def deduplicate_vs_db(state: GraphState) -> dict:
    """
    Node 1.5: Remove channels already emailed (exist in outreach_emails table).
    Writes the filtered list to `deduped_channels` (plain overwrite field).
    Channels seen before but not yet emailed are kept for fresh re-enrichment.
    """
    db = Database()
    emailed_ids = db.get_emailed_channel_ids()
    raw = state.get("raw_channels", [])

    kept: list[dict] = []
    skipped: list[str] = []

    for ch in raw:
        cid = ch.get("channel_id")
        if cid and cid in emailed_ids:
            skipped.append(cid)
        else:
            kept.append(ch)

    log.info("deduplicate_vs_db — %d raw, %d skipped (already emailed), %d proceed",
             len(raw), len(skipped), len(kept))

    return {
        "deduped_channels": kept,
        "skipped_channel_ids": skipped,
        "current_phase": "dedup_complete",
    }
