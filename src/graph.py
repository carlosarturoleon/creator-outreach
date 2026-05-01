from langgraph.graph import StateGraph, START, END

from src.logger import get_logger
from src.state import GraphState

log = get_logger(__name__)
from src.nodes.search_channels import search_channels
from src.nodes.deduplicate_vs_db import deduplicate_vs_db
from src.nodes.pre_filter_by_description import pre_filter_by_description
from src.nodes.enrich_channel_data import enrich_channel_data
from src.nodes.scrape_contact_emails import scrape_contact_emails
from src.nodes.filter_influencers import filter_influencers
from src.nodes.score_influencers import score_influencers
from src.nodes.llm_score_influencers import llm_score_influencers
from src.nodes.generate_emails import generate_emails
from src.nodes.save_results import save_results


def _route_after_dedup(state: GraphState) -> str:
    """Skip to END if all channels were already emailed."""
    if not state.get("deduped_channels"):
        log.info("All found channels already emailed — nothing new to process")
        return "__end__"
    return "pre_filter_by_description"


def _route_after_filter(state: GraphState) -> str:
    """Skip to END if no channels passed the filters, or if preview mode is on."""
    filtered = state.get("filtered_channels", [])
    if not filtered:
        log.warning("No channels passed the filters — try adjusting thresholds")
        return "__end__"
    if state.get("stop_after_filter", False):
        _print_filter_preview(filtered)
        return "__end__"
    return "score_influencers"


def _print_filter_preview(channels: list) -> None:
    print(f"\n{'='*60}")
    print("PREVIEW — Channels Passing All Filters")
    print(f"{'='*60}")
    print(f"  {len(channels)} channel(s) passed\n")
    for i, ch in enumerate(channels, 1):
        desc = (ch.get("description", "") or "")[:120].replace("\n", " ")
        print(f"  [{i}] {ch.get('channel_title', 'Unknown')}")
        print(f"       ID:          {ch.get('channel_id', '')}")
        print(f"       Subscribers: {ch.get('subscriber_count', 0):,}")
        print(f"       Engagement:  {ch.get('engagement_rate', 0.0):.2f}%")
        print(f"       Keyword:     {ch.get('search_keyword', '')}")
        print(f"       Description: {desc}...")
        print()
    print(f"{'='*60}")
    print("  Run without --stop-after-filter to score and generate emails.")
    print(f"{'='*60}\n")


def build_graph():
    builder = StateGraph(GraphState)

    # Register nodes
    builder.add_node("search_channels", search_channels)
    builder.add_node("deduplicate_vs_db", deduplicate_vs_db)
    builder.add_node("pre_filter_by_description", pre_filter_by_description)
    builder.add_node("enrich_channel_data", enrich_channel_data)
    builder.add_node("scrape_contact_emails", scrape_contact_emails)
    builder.add_node("filter_influencers", filter_influencers)
    builder.add_node("score_influencers", score_influencers)
    builder.add_node("llm_score_influencers", llm_score_influencers)
    builder.add_node("generate_emails", generate_emails)
    builder.add_node("save_results", save_results)

    # Edges
    builder.add_edge(START, "search_channels")
    builder.add_edge("search_channels", "deduplicate_vs_db")

    builder.add_conditional_edges(
        "deduplicate_vs_db",
        _route_after_dedup,
        {"pre_filter_by_description": "pre_filter_by_description", "__end__": END},
    )
    builder.add_edge("pre_filter_by_description", "enrich_channel_data")

    builder.add_edge("enrich_channel_data", "filter_influencers")

    builder.add_conditional_edges(
        "filter_influencers",
        _route_after_filter,
        {"score_influencers": "score_influencers", "__end__": END},
    )
    # Note: _route_after_filter returns "__end__" for both empty results and
    # stop_after_filter mode — both map to END above.

    builder.add_edge("score_influencers", "llm_score_influencers")
    builder.add_edge("llm_score_influencers", "scrape_contact_emails")
    builder.add_edge("scrape_contact_emails", "generate_emails")
    builder.add_edge("generate_emails", "save_results")
    builder.add_edge("save_results", END)

    return builder.compile()
