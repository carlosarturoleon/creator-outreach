"""
One-shot test: regenerate the email for Natalia Acevedo and print the result.
Does NOT write to the database.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.tools.batch_email_client import build_email_requests, submit_batch, wait_for_batch, fetch_email_results

influencer = {
    "channel_id": "UCsFI_rzW-8Z9PmwZTL_Gohw",
    "channel_title": "Natalia Acevedo",
    "subscriber_count": 0,
    "engagement_rate": 0.0,
    "llm_rationale": (
        "Natalia is a digital data analyst who explicitly teaches Looker Studio (full courses), "
        "Google BigQuery, Google Ads, and Facebook Ads — all core product data sources and "
        "destinations. Her recent video titles and channel description directly overlap with "
        "the product's exact use case of connecting ad platforms to BI tools like Looker Studio and BigQuery."
    ),
    "niche_tags": ["Looker Studio", "Google Ads", "Facebook Ads"],
}

enriched_map = {
    "UCsFI_rzW-8Z9PmwZTL_Gohw": {
        "description": "Hola! Soy analista digital de datos ✨ Te enseño a tomar decisiones basadas en datos ✨ Data studio • Facebook ads • Google ...",
        "recent_video_titles": [],
    }
}

requests = build_email_requests([influencer], enriched_map)
batch_id = submit_batch(requests)
print(f"Batch submitted: {batch_id}")
wait_for_batch(batch_id)
results = fetch_email_results(batch_id)

result = results.get("UCsFI_rzW-8Z9PmwZTL_Gohw", {})
print("\n=== SUBJECT ===")
print(result.get("subject_line", "N/A"))
print("\n=== BODY ===")
print(result.get("email_body", "N/A"))
print("\n=== SUCCESS ===", result.get("success"))
