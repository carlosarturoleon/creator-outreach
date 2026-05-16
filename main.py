import argparse
import math
import sys
import uuid
from pathlib import Path

from src.config import settings
from src.db.database import Database
from src.graph import build_graph, build_from_db_graph
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
    db_init.migrate_llm_scoring()
    db_init.migrate_add_no_email()
    db_init.migrate_add_contact_emails()
    db_init.migrate_add_daily_quota()
    db_init.migrate_add_quota_to_runs()

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
        default=1000,
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
        default=[],
        help="Target languages to filter by (ISO 639-1 codes, e.g. 'en es'). Default: no language filter.",
    )
    parser.add_argument(
        "--keywords-file2",
        type=Path,
        default=None,
        dest="keywords_file2",
        help="Optional second keywords file to append (one keyword per line, # = comment)",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=20,
        dest="max_results",
        help="Max channel results per keyword",
    )
    parser.add_argument(
        "--max-seed-channels",
        type=int,
        default=10,
        dest="max_seed_channels",
        help="Max seed channels to use for related-channel traversal in discover_channels",
    )
    parser.add_argument(
        "--stop-after-filter",
        action="store_true",
        dest="stop_after_filter",
        help="Stop after filtering. Print results without scoring or email generation.",
    )
    parser.add_argument(
        "--from-db",
        action="store_true",
        dest="from_db",
        help=(
            "Skip search/discover/enrich. Load channels already in the DB and "
            "run filter → score → generate emails on them."
        ),
    )
    parser.add_argument(
        "--since-date",
        type=str,
        default=None,
        dest="since_date",
        help="With --from-db: only load channels last updated on or after this date (YYYY-MM-DD). "
             "Defaults to today.",
    )
    parser.add_argument(
        "--quota-budget",
        type=int,
        default=8000,
        dest="quota_budget",
        help="Max YouTube API units to spend before skipping discover_channels (daily limit is 10,000).",
    )
    parser.add_argument(
        "--force-reenrich",
        action="store_true",
        dest="force_reenrich",
        help="Bypass the enrichment cache and re-fetch stats from YouTube for all channels.",
    )
    args = parser.parse_args()

    # --- Resolve keywords (not required for --from-db) ---
    keywords = []
    if not args.from_db:
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
        if args.keywords_file2 and args.keywords_file2.exists():
            lines2 = args.keywords_file2.read_text().splitlines()
            extra = [l.strip() for l in lines2 if l.strip() and not l.strip().startswith("#")]
            if extra:
                keywords = list(dict.fromkeys(keywords + extra))
                print(f"Loaded {len(extra)} additional keywords from {args.keywords_file2} (total: {len(keywords)})")
    if args.min_subscribers < 0:
        print("Error: --min-subscribers must be >= 0.")
        sys.exit(1)
    if args.min_engagement < 0:
        print("Error: --min-engagement must be >= 0.")
        sys.exit(1)
    if not args.from_db and (args.max_results < 1 or args.max_results > 50):
        print("Error: --max-results must be between 1 and 50 (YouTube API limit).")
        sys.exit(1)

    # --- Quota planning: persistent daily tracking across runs ---
    num_keys = len(settings.youtube_api_keys)
    total_daily_budget = 10_000 * num_keys
    db = Database()
    spent_today = db.get_quota_spent_today()
    remaining_quota = total_daily_budget - spent_today

    if remaining_quota <= 0:
        print(f"\nDaily quota exhausted: {spent_today:,}/{total_daily_budget:,} units used today "
              f"({num_keys} key(s) × 10,000). Try again tomorrow.")
        sys.exit(0)

    # Estimate mandatory step costs to determine how much is left for discovery
    if not args.from_db:
        est_search = len(keywords) * 100
        est_new_channels = len(keywords) * args.max_results
        est_enrich = math.ceil(est_new_channels / 50) + est_new_channels * 3
        est_mandatory = est_search + est_enrich
        est_discovery = max(0, remaining_quota - est_mandatory)
    else:
        est_search = est_enrich = est_mandatory = est_discovery = 0

    # Respect manual --quota-budget cap if set lower than remaining
    manual_cap = args.quota_budget * num_keys
    effective_quota_budget = min(remaining_quota, manual_cap)

    run_id = str(uuid.uuid4())
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
    log.info("Min subscribers: %s", f"{args.min_subscribers:,}")
    log.info("Max subscribers: %s", f"{args.max_subscribers:,}" if args.max_subscribers else "no cap")
    log.info("Min engagement:  %s%%", args.min_engagement)
    log.info("Languages:       %s", args.languages)
    if args.from_db:
        log.info("Mode: FROM-DB (filter/score/email on existing DB channels)")
    else:
        log.info("Keywords:        %s", keywords)
        log.info("Max results:     %s per keyword", args.max_results)
        log.info("Max seed chans:  %s (related traversal)", args.max_seed_channels)
        log.info("Quota today:     %d/%d used (%d remaining)", spent_today, total_daily_budget, remaining_quota)
        log.info("Quota est:       search=%d enrich=%d discovery≤%d", est_search, est_enrich, est_discovery)
        log.info("Quota budget:    %d units (effective cap)", effective_quota_budget)
    if args.force_reenrich:
        log.info("Force reenrich:  ON (bypassing enrichment cache)")
    if args.stop_after_filter:
        log.info("Mode: PREVIEW (stops after filter)")

    print(f"\nWindsor.ai Influencer Finder  [run_id: {run_id}]")
    print(f"Min subscribers: {args.min_subscribers:,}")
    print(f"Max subscribers: {args.max_subscribers:,}" if args.max_subscribers else "Max subscribers: no cap")
    print(f"Min engagement:  {args.min_engagement}%")
    print(f"Languages:       {args.languages}")
    if args.from_db:
        print("Mode:            FROM-DB (filter/score/email on existing DB channels)")
    else:
        print(f"Keywords:        {keywords}")
        print(f"Max results:     {args.max_results} per keyword")
        print(f"Quota today:     {spent_today:,}/{total_daily_budget:,} used  ({remaining_quota:,} remaining)")
        print(f"Quota estimate:  search={est_search}  enrich={est_enrich}  discovery≤{est_discovery}")
        print(f"Effective cap:   {effective_quota_budget:,} units")
    if args.stop_after_filter:
        print("Mode:            PREVIEW (stops after filter — no LLM scoring or emails)")
    print()

    if args.from_db:
        import datetime
        since = args.since_date or datetime.date.today().isoformat()
        graph = build_from_db_graph()
        all_channels = db.get_all_channels(since_date=since)
        log.info("--from-db: loaded %d channels updated since %s", len(all_channels), since)
        print(f"Loaded {len(all_channels):,} channels updated since {since}")
        initial_state = {
            "search_keywords": [],
            "min_subscribers": args.min_subscribers,
            "max_subscribers": args.max_subscribers,
            "min_engagement_rate": args.min_engagement,
            "target_languages": args.languages,
            "max_results_per_keyword": args.max_results,
            "max_seed_channels": args.max_seed_channels,
            "quota_budget": effective_quota_budget,
            "enrich_quota_reserve": 0,
            "force_reenrich": args.force_reenrich,
            "stop_after_filter": args.stop_after_filter,
            "run_id": run_id,
            "raw_channels": [],
            "deduped_channels": [],
            "pre_filtered_channels": [],
            "enriched_channels": all_channels,
            "filtered_channels": [],
            "pre_llm_influencers": [],
            "scored_influencers": [],
            "outreach_emails": [],
            "error_log": [],
            "skipped_channel_ids": [],
            "quota_units_spent": 0,
            "current_phase": "enrichment_complete",
        }
    else:
        graph = build_graph()
        initial_state = {
            "search_keywords": keywords,
            "min_subscribers": args.min_subscribers,
            "max_subscribers": args.max_subscribers,
            "min_engagement_rate": args.min_engagement,
            "target_languages": args.languages,
            "max_results_per_keyword": args.max_results,
            "max_seed_channels": args.max_seed_channels,
            "quota_budget": effective_quota_budget,
            "enrich_quota_reserve": est_enrich,
            "force_reenrich": args.force_reenrich,
            "stop_after_filter": args.stop_after_filter,
            "run_id": run_id,
            "raw_channels": [],
            "deduped_channels": [],
            "pre_filtered_channels": [],
            "enriched_channels": [],
            "filtered_channels": [],
            "pre_llm_influencers": [],
            "scored_influencers": [],
            "outreach_emails": [],
            "error_log": [],
            "skipped_channel_ids": [],
            "quota_units_spent": 0,
            "current_phase": "initializing",
        }

    try:
        final_state = graph.invoke(initial_state)
        quota_spent_this_run = final_state.get("quota_units_spent", 0)
        db.add_quota_spent(quota_spent_this_run)
        db.finish_run(run_id, {
            "total_found": len(final_state.get("raw_channels", [])),
            "total_deduped": len(final_state.get("deduped_channels", [])),
            "total_pre_filtered": len(final_state.get("pre_filtered_channels", [])),
            "total_enriched": len(final_state.get("enriched_channels", [])),
            "total_filtered": len(final_state.get("filtered_channels", [])),
            "total_scored": len(final_state.get("scored_influencers", [])),
            "total_emailed": len(final_state.get("outreach_emails", [])),
            "error_count": len(final_state.get("error_log", [])),
            "quota_units_spent": quota_spent_this_run,
        })
        log.info("Quota spent this run: %d units (today total: %d/%d)",
                 quota_spent_this_run, spent_today + quota_spent_this_run, total_daily_budget)
    except Exception as e:
        log.error("Pipeline failed: %s", e)
        db.finish_run(run_id, {"error_count": 1}, status="failed")
        raise


if __name__ == "__main__":
    main()
