from src.state import GraphState
from src.db.database import Database

WINDSOR_AI_NICHES = {
    "marketing", "analytics", "attribution", "digital marketing",
    "seo", "ppc", "advertising", "data", "saas", "performance marketing",
    "google analytics", "facebook ads", "media buying", "growth hacking",
    "ecommerce", "bi", "business intelligence", "tracking", "data studio",
    "looker", "bigquery", "spreadsheet", "dashboard", "conversion",
    "affiliate", "paid ads", "paid media", "martech", "marketing technology",
    "ads manager", "google ads", "tiktok ads", "linkedin ads",
}


def filter_influencers(state: GraphState) -> dict:
    """
    Node 3: Apply hard and soft filters.

    Hard filters (all must pass):
      1. subscriber_count >= min_subscribers
      2. engagement_rate >= min_engagement_rate
      3. Language match (permissive — only blocks confirmed mismatches)

    Soft filter (at least one Windsor.ai niche keyword must appear):
      4. Niche relevance via keyword match in description + keywords + video titles
    """
    filtered: list[dict] = []
    target_langs = [lang.lower()[:2] for lang in state.get("target_languages", [])]
    min_subs = state.get("min_subscribers", 0)
    min_eng = state.get("min_engagement_rate", 0.0)

    for ch in state.get("enriched_channels", []):
        # Hard filter 1: subscribers
        if ch.get("subscriber_count", 0) < min_subs:
            continue

        # Hard filter 2: engagement rate
        if ch.get("engagement_rate", 0.0) < min_eng:
            continue

        # Hard filter 3: language (only block if we have confirmed non-matching language)
        if target_langs:
            ch_lang = (ch.get("default_language") or "").lower()[:2]
            if ch_lang and ch_lang not in target_langs:
                continue

        # Soft filter 4: Windsor.ai niche keyword match
        text_blob = " ".join([
            ch.get("description", ""),
            " ".join(ch.get("keywords", [])),
            " ".join(ch.get("recent_video_titles", [])),
            ch.get("channel_title", ""),
        ]).lower()

        if not any(niche in text_blob for niche in WINDSOR_AI_NICHES):
            continue

        filtered.append(ch)

    print(f"[filter_influencers] {len(filtered)} channels passed filters (from {len(state.get('enriched_channels', []))})")

    if filtered:
        try:
            Database().mark_channels_filtered([ch["channel_id"] for ch in filtered])
        except Exception as e:
            print(f"[filter_influencers] DB mark failed: {e}")

    return {
        "filtered_channels": filtered,
        "current_phase": "filtering_complete",
    }
