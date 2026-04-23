"""
llm_score_from_db.py — Send all deterministically-scored-but-not-LLM-scored
channels to Claude via Anthropic Batch API and persist the results.

Picks up where filter_and_score_from_db.py left off. Channels that already
have an llm_score are skipped (idempotent).

Usage:
    python scripts/llm_score_from_db.py
    python scripts/llm_score_from_db.py --floor 5   # only keep scores >= 5 (default 4)
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config import settings
from src.db.database import Database
from src.tools.batch_scorer_client import (
    build_scorer_requests,
    fetch_scorer_results,
    submit_batch,
    wait_for_batch,
)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "output", "influencers.db")


def load_unscored(db: Database) -> tuple[list[dict], dict[str, dict]]:
    """Return (influencers, enriched_map) for channels missing llm_score."""
    with db._connect() as conn:
        rows = conn.execute("""
            SELECT
                s.channel_id, s.composite_score, s.relevance_rationale, s.niche_tags,
                c.channel_title, c.subscriber_count, c.engagement_rate,
                c.description, c.keywords, c.recent_video_titles
            FROM scored_influencers s
            JOIN channels c ON s.channel_id = c.channel_id
            WHERE s.llm_score IS NULL
            ORDER BY s.composite_score DESC
        """).fetchall()

    influencers = []
    enriched_map = {}
    for row in rows:
        d = dict(row)
        cid = d["channel_id"]

        for field in ("niche_tags", "keywords", "recent_video_titles"):
            raw = d.get(field)
            try:
                d[field] = json.loads(raw) if raw else []
            except (json.JSONDecodeError, TypeError):
                d[field] = []

        influencers.append({
            "channel_id": cid,
            "channel_title": d["channel_title"],
            "subscriber_count": d["subscriber_count"],
            "engagement_rate": d["engagement_rate"],
            "composite_score": d["composite_score"],
            "relevance_rationale": d["relevance_rationale"] or "",
            "niche_tags": d["niche_tags"],
            "llm_score": None,
            "llm_rationale": None,
        })
        enriched_map[cid] = {
            "channel_id": cid,
            "description": d["description"] or "",
            "keywords": d["keywords"],
            "recent_video_titles": d["recent_video_titles"],
        }

    return influencers, enriched_map


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM-score channels via Anthropic Batch API")
    parser.add_argument("--floor", type=int, default=4, help="Minimum llm_score to keep (default 4)")
    args = parser.parse_args()

    db = Database()
    influencers, enriched_map = load_unscored(db)

    if not influencers:
        print("All scored channels already have an LLM score.")
        print("Run `python regenerate_emails.py` to generate emails.")
        sys.exit(0)

    print(f"Found {len(influencers)} channel(s) without an LLM score.")
    print(f"Submitting to Claude (model: {settings.claude_model})...\n")

    requests = build_scorer_requests(
        influencers=influencers,
        enriched_map=enriched_map,
        model=settings.claude_model,
    )

    try:
        batch_id = submit_batch(requests)
    except Exception as e:
        print(f"Batch submission failed: {e}")
        sys.exit(1)

    wait_for_batch(batch_id)
    results = fetch_scorer_results(batch_id)

    passed = 0
    dropped = 0
    for influencer in influencers:
        cid = influencer["channel_id"]
        result = results.get(cid, {})

        llm_score = result.get("llm_score", 0) if result.get("success") else 0
        llm_rationale = result.get("llm_rationale", "Score unavailable.")

        updated = {**influencer, "llm_score": llm_score, "llm_rationale": llm_rationale}
        db.upsert_scored_influencer(updated)

        if llm_score >= args.floor:
            passed += 1
            print(f"  [{llm_score}/10] {influencer['channel_title']}")
        else:
            dropped += 1

    print(f"\n{passed} passed (score >= {args.floor}), {dropped} dropped.")
    print("\nNext steps:")
    print("  1. Add contact emails:  python scripts/set_email.py \"Channel\" email@example.com")
    print("  2. Generate emails:     python regenerate_emails.py")
    print("  3. Send:                python send_emails.py --dry-run")


if __name__ == "__main__":
    main()
