from src.state import GraphState
from src.nodes.filter_influencers import WINDSOR_AI_NICHES


def pre_filter_by_description(state: GraphState) -> dict:
    """
    Node 1.7: Description-based pre-filter before enrichment.

    Drops channels whose description contains no Windsor.ai niche keywords,
    saving ~3 YouTube API quota units per dropped channel (video stats calls).

    Fail-open: channels with very short/missing descriptions (<30 chars) are
    kept and passed to full enrichment + filtering to decide.
    """
    kept: list[dict] = []
    dropped = 0

    for ch in state.get("deduped_channels", []):
        desc = (ch.get("description", "") or "").lower()
        if len(desc) < 30 or any(niche in desc for niche in WINDSOR_AI_NICHES):
            kept.append(ch)
        else:
            dropped += 1

    print(f"[pre_filter] {len(kept)} kept, {dropped} dropped by description pre-filter")
    return {"pre_filtered_channels": kept, "current_phase": "pre_filter_complete"}
