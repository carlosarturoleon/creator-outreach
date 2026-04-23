from src.logger import get_logger
from src.state import GraphState
from src.db.database import Database

log = get_logger(__name__)

WINDSOR_AI_NICHES = {
    # Core marketing / attribution
    "marketing", "analytics", "attribution", "digital marketing",
    "seo", "ppc", "performance marketing", "paid advertising",
    "conversion tracking", "media buying", "growth hacking",
    "affiliate", "paid ads", "paid media", "martech", "marketing technology",
    # Windsor.ai data sources
    "google analytics", "ga4", "gtm", "google tag manager",
    "facebook ads", "meta ads", "google ads", "tiktok ads", "linkedin ads",
    "pinterest ads", "snapchat ads", "amazon ads", "amazon seller",
    "hubspot", "salesforce", "shopify", "stripe",
    # Windsor.ai destinations
    "looker studio", "data studio", "looker", "bigquery", "snowflake",
    "amazon redshift", "power bi", "tableau", "google sheets",
    "microsoft excel",
    # Broader niche
    "ecommerce", "saas", "ads manager", "data",
    "business intelligence",
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
    max_subs = state.get("max_subscribers", 0)  # 0 means no cap
    min_eng = state.get("min_engagement_rate", 0.0)

    for ch in state.get("enriched_channels", []):
        # Hard filter 1: subscribers (min and optional max cap)
        subs = ch.get("subscriber_count", 0)
        if subs < min_subs:
            continue
        if max_subs and subs > max_subs:
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

    total = len(state.get("enriched_channels", []))
    log.info("filter_influencers — %d/%d channels passed all filters", len(filtered), total)

    if filtered:
        try:
            Database().mark_channels_filtered([ch["channel_id"] for ch in filtered])
        except Exception as e:
            log.error("filter_influencers — DB mark failed: %s", e)

    return {
        "filtered_channels": filtered,
        "current_phase": "filtering_complete",
    }
