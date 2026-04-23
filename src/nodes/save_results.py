import csv
import json
import os
from datetime import datetime

from src.state import GraphState
from src.config import settings


def save_results(state: GraphState) -> dict:
    """
    Node 6: Write scored influencers + emails to timestamped CSV and JSON files.
    Prints a pipeline summary to stdout.
    """
    os.makedirs(settings.output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(settings.output_dir, f"influencers_{timestamp}.csv")
    json_path = os.path.join(settings.output_dir, f"influencers_{timestamp}.json")

    email_map: dict[str, dict] = {
        e["channel_id"]: e for e in state.get("outreach_emails", [])
    }

    rows: list[dict] = []
    for i, inf in enumerate(state.get("scored_influencers", []), start=1):
        email = email_map.get(inf["channel_id"], {})
        rows.append({
            "rank": i,
            "channel_id": inf["channel_id"],
            "channel_title": inf["channel_title"],
            "subscriber_count": inf["subscriber_count"],
            "engagement_rate": inf["engagement_rate"],
            "composite_score": inf["composite_score"],
            "engagement_score": inf.get("score_breakdown", {}).get("engagement", 0.0),
            "audience_size_score": inf.get("score_breakdown", {}).get("audience_size", 0.0),
            "relevance_score": inf.get("score_breakdown", {}).get("relevance", 0.0),
            "niche_tags": "|".join(inf.get("niche_tags", [])),
            "relevance_rationale": inf.get("relevance_rationale", ""),
            "email_subject": email.get("subject_line", ""),
            "email_body": email.get("email_body", ""),
        })

    if rows:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    full_output = {
        "run_timestamp": timestamp,
        "total_found": len(state.get("raw_channels", [])),
        "total_deduped": len(state.get("deduped_channels", [])),
        "total_skipped": len(state.get("skipped_channel_ids", [])),
        "total_enriched": len(state.get("enriched_channels", [])),
        "total_filtered": len(state.get("filtered_channels", [])),
        "total_scored": len(state.get("scored_influencers", [])),
        "errors": state.get("error_log", []),
        "influencers": rows,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(full_output, f, indent=2, ensure_ascii=False)

    # LLM score stats
    llm_scores = [
        inf["llm_score"] for inf in state.get("scored_influencers", [])
        if inf.get("llm_score") is not None
    ]
    if llm_scores:
        llm_avg = sum(llm_scores) / len(llm_scores)
        llm_min = min(llm_scores)
        llm_max = max(llm_scores)
    else:
        llm_avg = llm_min = llm_max = None

    # Email stats
    emails = state.get("outreach_emails", [])
    emails_with_contact = sum(1 for e in emails if e.get("contact_email"))

    # Print summary
    print(f"\n{'='*60}")
    print("Windsor.ai Influencer Finder — Run Complete")
    print(f"{'='*60}")
    print(f"  Channels found:       {full_output['total_found']}")
    print(f"  Already emailed:      {full_output['total_skipped']} (skipped)")
    print(f"  After dedup:          {full_output['total_deduped']}")
    print(f"  After enrichment:     {full_output['total_enriched']}")
    print(f"  After filtering:      {full_output['total_filtered']}")
    print(f"  Scored & ranked:      {full_output['total_scored']}")
    if llm_avg is not None:
        print(f"  LLM score:            avg {llm_avg:.1f} | min {llm_min} | max {llm_max} ({len(llm_scores)} scored)")
    print(f"  Emails generated:     {len(emails)} ({emails_with_contact} with contact email, {len(emails) - emails_with_contact} missing)")
    if full_output["errors"]:
        print(f"  Errors logged:        {len(full_output['errors'])}")
    print(f"  Output CSV:           {csv_path}")
    print(f"  Output JSON:          {json_path}")
    print(f"  Database:             {settings.output_dir}/influencers.db")
    print(f"{'='*60}\n")

    return {"current_phase": "complete"}
