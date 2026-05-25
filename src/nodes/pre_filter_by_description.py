from src.logger import get_logger
from src.state import GraphState
from src.nodes.filter_influencers import NICHE_KEYWORDS
from src.scoring.keyword_scorer import NEGATIVE_KEYWORDS

log = get_logger(__name__)


def pre_filter_by_description(state: GraphState) -> dict:
    """
    Node 1.7: Description-based pre-filter before enrichment.

    Two-pass filter on the channel description:
      1. Reject if any negative keyword is present (crypto, gaming, lifestyle…)
      2. Reject if no niche keyword is found

    Fail-open: channels with very short/missing descriptions (<30 chars) skip
    the niche check (kept), but negative keywords still disqualify them.

    Saves ~3 YouTube API quota units per dropped channel.
    """
    kept: list[dict] = []
    dropped_negative = 0
    dropped_no_niche = 0

    for ch in state.get("deduped_channels", []):
        desc = (ch.get("description", "") or "").lower()
        title = (ch.get("channel_title", "") or "").lower()
        text = desc + " " + title

        # Hard reject: negative keyword found
        if any(neg in text for neg in NEGATIVE_KEYWORDS):
            dropped_negative += 1
            continue

        # Fail-open on short descriptions; otherwise require a niche keyword
        if len(desc) >= 30 and not any(niche in text for niche in NICHE_KEYWORDS):
            dropped_no_niche += 1
            continue

        kept.append(ch)

    log.info(
        "pre_filter_by_description — %d kept, %d dropped (negative kw), %d dropped (no niche)",
        len(kept), dropped_negative, dropped_no_niche,
    )
    return {"pre_filtered_channels": kept, "current_phase": "pre_filter_complete"}
