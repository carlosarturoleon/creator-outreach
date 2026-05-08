import operator
from typing import Annotated
from typing_extensions import TypedDict


class GraphState(TypedDict):
    # --- Inputs ---
    search_keywords: list[str]
    min_subscribers: int
    max_subscribers: int
    min_engagement_rate: float
    target_languages: list[str]
    max_results_per_keyword: int
    max_seed_channels: int  # max seed channels for related-channel traversal

    # --- Pipeline data (operator.add = safe append, enables future parallelism) ---
    raw_channels: Annotated[list[dict], operator.add]
    # Plain overwrite: set once by deduplicate_vs_db, read by enrich_channel_data
    deduped_channels: list[dict]
    enriched_channels: Annotated[list[dict], operator.add]
    filtered_channels: Annotated[list[dict], operator.add]
    scored_influencers: Annotated[list[dict], operator.add]
    outreach_emails: Annotated[list[dict], operator.add]

    # --- Control / audit ---
    error_log: Annotated[list[str], operator.add]
    skipped_channel_ids: Annotated[list[str], operator.add]
    current_phase: str
    stop_after_filter: bool
    run_id: str
    # Plain overwrite: set by pre_filter_by_description, read by enrich_channel_data
    pre_filtered_channels: list[dict]
    # Plain overwrite: set by scrape_contact_emails, read by filter_influencers
    scraped_channels: list[dict]
