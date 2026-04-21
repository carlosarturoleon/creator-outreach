from src.state import GraphState
from src.tools.youtube_client import YouTubeClient


def search_channels(state: GraphState) -> dict:
    """
    Node 1: Search YouTube for channels matching each keyword.
    Deduplicates by channel_id across keywords.
    """
    client = YouTubeClient()
    seen_ids: set[str] = set()
    new_channels: list[dict] = []

    for keyword in state["search_keywords"]:
        try:
            results = client.search_channels(
                keyword=keyword,
                max_results=state["max_results_per_keyword"],
            )
            for ch in results:
                cid = ch.get("channel_id")
                if cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    new_channels.append(ch)
        except Exception as e:
            # Non-fatal: log and continue to next keyword
            print(f"[search_channels] Error for keyword '{keyword}': {e}")

    print(f"[search_channels] Found {len(new_channels)} unique channels across {len(state['search_keywords'])} keywords")
    return {
        "raw_channels": new_channels,
        "current_phase": "search_complete",
    }
