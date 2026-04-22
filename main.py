import argparse
import sys
import uuid
from pathlib import Path

from src.config import settings
from src.db.database import Database
from src.graph import build_graph
from src.logger import attach_db_handler, get_logger


def main() -> None:
    try:
        settings.validate()
    except ValueError as e:
        print(f"Configuration error: {e}")
        sys.exit(1)

    # Initialize DB schema once at startup
    db_init = Database()
    db_init.init_db()
    db_init.migrate_add_contact_email()
    db_init.migrate_scoring_v2()

    parser = argparse.ArgumentParser(
        description="Windsor.ai YouTube Influencer Finder",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--keywords",
        nargs="+",
        default=None,
        help="Search keywords (each as a separate argument)",
    )
    parser.add_argument(
        "--keywords-file",
        type=Path,
        default=Path("keywords.txt"),
        dest="keywords_file",
        help="Path to a text file with one keyword per line (# = comment)",
    )
    parser.add_argument(
        "--min-subscribers",
        type=int,
        default=5000,
        dest="min_subscribers",
        help="Minimum subscriber count",
    )
    parser.add_argument(
        "--max-subscribers",
        type=int,
        default=10000,
        dest="max_subscribers",
        help="Maximum subscriber count (0 = no cap)",
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

    # --- Resolve keywords (--keywords takes priority over --keywords-file) ---
    if args.keywords:
        keywords = args.keywords
    elif args.keywords_file.exists():
        lines = args.keywords_file.read_text().splitlines()
        keywords = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]
        if not keywords:
            print(f"Error: {args.keywords_file} contains no keywords.")
            sys.exit(1)
        print(f"Loaded {len(keywords)} keywords from {args.keywords_file}")
    else:
        print(f"Error: no --keywords given and {args.keywords_file} not found.")
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

    run_id = str(uuid.uuid4())
    db = Database()
    db.create_run(run_id, {
        "keywords": keywords,
        "min_subscribers": args.min_subscribers,
        "min_engagement_rate": args.min_engagement,
        "max_results_per_keyword": args.max_results,
        "stop_after_filter": args.stop_after_filter,
    })
    attach_db_handler(run_id)
    log = get_logger(__name__)

    log.info("Run ID: %s", run_id)
    log.info("Keywords:        %s", keywords)
    log.info("Min subscribers: %s", f"{args.min_subscribers:,}")
    log.info("Max subscribers: %s", f"{args.max_subscribers:,}" if args.max_subscribers else "no cap")
    log.info("Min engagement:  %s%%", args.min_engagement)
    log.info("Languages:       %s", args.languages)
    log.info("Max results:     %s per keyword", args.max_results)
    if args.stop_after_filter:
        log.info("Mode: PREVIEW (stops after filter)")

    print(f"\nWindsor.ai Influencer Finder  [run_id: {run_id}]")
    print(f"Keywords:        {keywords}")
    print(f"Min subscribers: {args.min_subscribers:,}")
    print(f"Max subscribers: {args.max_subscribers:,}" if args.max_subscribers else "Max subscribers: no cap")
    print(f"Min engagement:  {args.min_engagement}%")
    print(f"Languages:       {args.languages}")
    print(f"Max results:     {args.max_results} per keyword")
    if args.stop_after_filter:
        print("Mode:            PREVIEW (stops after filter — no LLM scoring or emails)")
    print()

    graph = build_graph()

    initial_state = {
        "search_keywords": keywords,
        "min_subscribers": args.min_subscribers,
        "max_subscribers": args.max_subscribers,
        "min_engagement_rate": args.min_engagement,
        "target_languages": args.languages,
        "max_results_per_keyword": args.max_results,
        "stop_after_filter": args.stop_after_filter,
        "run_id": run_id,
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

    try:
        final_state = graph.invoke(initial_state)
        db.finish_run(run_id, {
            "total_found": len(final_state.get("raw_channels", [])),
            "total_deduped": len(final_state.get("deduped_channels", [])),
            "total_pre_filtered": len(final_state.get("pre_filtered_channels", [])),
            "total_enriched": len(final_state.get("enriched_channels", [])),
            "total_filtered": len(final_state.get("filtered_channels", [])),
            "total_scored": len(final_state.get("scored_influencers", [])),
            "total_emailed": len(final_state.get("outreach_emails", [])),
            "error_count": len(final_state.get("error_log", [])),
        })
    except Exception as e:
        log.error("Pipeline failed: %s", e)
        db.finish_run(run_id, {"error_count": 1}, status="failed")
        raise


if __name__ == "__main__":
    main()
