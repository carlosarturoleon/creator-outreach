import argparse
import sys

from src.config import settings
from src.db.database import Database
from src.graph import build_graph


def main() -> None:
    try:
        settings.validate()
    except ValueError as e:
        print(f"Configuration error: {e}")
        sys.exit(1)

    # Initialize DB schema once at startup
    Database().init_db()

    parser = argparse.ArgumentParser(
        description="Windsor.ai YouTube Influencer Finder",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--keywords",
        nargs="+",
        default=[
            "marketing analytics",
            "marketing attribution",
            "digital marketing tools",
            "performance marketing",
            "google analytics tutorial",
        ],
        help="Search keywords (each as a separate argument)",
    )
    parser.add_argument(
        "--min-subscribers",
        type=int,
        default=5000,
        dest="min_subscribers",
        help="Minimum subscriber count",
    )
    parser.add_argument(
        "--min-engagement",
        type=float,
        default=1.0,
        dest="min_engagement",
        help="Minimum engagement rate (percent)",
    )
    parser.add_argument(
        "--languages",
        nargs="+",
        default=["en"],
        help="Target languages (ISO 639-1 codes)",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=20,
        dest="max_results",
        help="Max channel results per keyword",
    )
    parser.add_argument(
        "--stop-after-filter",
        action="store_true",
        dest="stop_after_filter",
        help="Stop after filtering. Print results without scoring or email generation.",
    )
    args = parser.parse_args()

    # --- Input validation ---
    if not args.keywords:
        print("Error: --keywords requires at least one value.")
        sys.exit(1)
    if args.min_subscribers < 0:
        print("Error: --min-subscribers must be >= 0.")
        sys.exit(1)
    if args.min_engagement < 0:
        print("Error: --min-engagement must be >= 0.")
        sys.exit(1)
    if args.max_results < 1 or args.max_results > 50:
        print("Error: --max-results must be between 1 and 50 (YouTube API limit).")
        sys.exit(1)

    print(f"\nWindsor.ai Influencer Finder")
    print(f"Keywords:        {args.keywords}")
    print(f"Min subscribers: {args.min_subscribers:,}")
    print(f"Min engagement:  {args.min_engagement}%")
    print(f"Languages:       {args.languages}")
    print(f"Max results:     {args.max_results} per keyword")
    if args.stop_after_filter:
        print("Mode:            PREVIEW (stops after filter — no LLM scoring or emails)")
    print()

    graph = build_graph()

    initial_state = {
        "search_keywords": args.keywords,
        "min_subscribers": args.min_subscribers,
        "min_engagement_rate": args.min_engagement,
        "target_languages": args.languages,
        "max_results_per_keyword": args.max_results,
        "stop_after_filter": args.stop_after_filter,
        # All Annotated[list] fields must be initialized to [] for operator.add
        "raw_channels": [],
        "deduped_channels": [],
        "pre_filtered_channels": [],
        "enriched_channels": [],
        "filtered_channels": [],
        "scored_influencers": [],
        "outreach_emails": [],
        "error_log": [],
        "skipped_channel_ids": [],
        "current_phase": "initializing",
    }

    graph.invoke(initial_state)


if __name__ == "__main__":
    main()
