"""
Deterministic keyword-based relevance scoring for Windsor.ai influencer candidates.

Replaces LLM scoring with a rule-based system using weighted keyword matching
against the channel description, title, keywords, and recent video titles.

Scoring tiers (description-focused):
  HIGH_VALUE   = 3 pts each  — direct Windsor.ai use-case signals
  MEDIUM_VALUE = 2 pts each  — paid advertising / performance marketing
  LOW_VALUE    = 1 pt each   — general digital marketing

Composite score (0-30, scaled to match the old LLM relevance range):
  (keyword_pts × 0.6) + (upload_freq_pts × 0.2) + (view_ratio_pts × 0.2)
  capped at 30.

Negative keywords cause immediate disqualification (score = 0).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Keyword dictionaries
# ---------------------------------------------------------------------------

HIGH_VALUE: list[str] = [
    "google analytics", "ga4",
    "attribution",
    "conversion tracking",
    "marketing analytics",
    "looker studio", "data studio",
    "google tag manager", "gtm",
    "facebook pixel", "meta pixel",
    "ecommerce analytics",
    "shopify analytics",
    "multi-channel tracking",
    "marketing attribution",
    "analytics dashboard",
    "marketing data",
    "supermetrics",                 # competitor — they already buy tools like Windsor
    "windsor",
]

MEDIUM_VALUE: list[str] = [
    "ppc", "paid advertising",
    "google ads",
    "facebook ads", "meta ads",
    "performance marketing",
    "roi tracking", "roi measurement",
    "marketing measurement",
    "data-driven marketing",
    "digital analytics",
    "tiktok ads",
    "linkedin ads",
    "ad performance",
    "shopify",
    "bigquery",
    "looker",
    "tableau",
    "power bi",
    "ads manager",
    "marketing dashboard",
    "affiliate marketing",
    "martech",
]

LOW_VALUE: list[str] = [
    "digital marketing",
    "marketing tips",
    "business growth",
    "social media marketing",
    "seo",
    "content marketing",
    "email marketing",
    "lead generation",
    "growth hacking",
    "saas",
    "analytics",
    "data",
    "ecommerce",
    "dropshipping",
]

NEGATIVE_KEYWORDS: list[str] = [
    "crypto", "nft", "web3", "blockchain", "defi",
    "lifestyle", "vlog", "daily vlog", "travel vlog",
    "gaming", "gameplay", "let's play",
    "entertainment", "music", "comedy",
    "fitness", "workout", "gym",
    "cooking", "recipe", "food",
    "fashion", "beauty", "makeup",
    "kids", "family", "parenting",
    "passive income secrets", "get rich",
    "reposting clips",
]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _build_text_blob(ch: dict) -> str:
    """Concatenate all text fields into a single lowercase string for matching."""
    parts = [
        ch.get("description", "") or "",
        ch.get("channel_title", "") or "",
        " ".join(ch.get("keywords", []) or []),
        " ".join(ch.get("recent_video_titles", []) or []),
    ]
    return " ".join(parts).lower()


def _keyword_points(text: str) -> tuple[int, list[str]]:
    """Return (total_points, matched_keyword_list)."""
    matched: list[str] = []
    points = 0
    for kw in HIGH_VALUE:
        if kw in text:
            matched.append(kw)
            points += 3
    for kw in MEDIUM_VALUE:
        if kw in text and kw not in matched:
            matched.append(kw)
            points += 2
    for kw in LOW_VALUE:
        if kw in text and kw not in matched:
            matched.append(kw)
            points += 1
    return points, matched


def _upload_freq_score(upload_frequency_days: float) -> float:
    """0-10 pts based on upload cadence."""
    if upload_frequency_days <= 0:
        return 0.0
    if upload_frequency_days <= 7:
        return 10.0   # weekly or more
    if upload_frequency_days <= 14:
        return 5.0    # bi-weekly
    if upload_frequency_days <= 30:
        return 2.0    # monthly
    return 0.0


def _view_ratio_score(avg_views: float, subscribers: int) -> float:
    """0-10 pts based on avg_views / subscribers ratio (capped)."""
    if subscribers <= 0 or avg_views <= 0:
        return 0.0
    ratio = (avg_views / subscribers) * 100  # as percentage
    return min(10.0, ratio)


def _infer_niche_tags(matched_keywords: list[str]) -> list[str]:
    """Map matched keywords to human-readable niche tags (deduped, max 5)."""
    tag_map = {
        "google analytics": "Google Analytics",
        "ga4": "GA4",
        "attribution": "Marketing Attribution",
        "conversion tracking": "Conversion Tracking",
        "marketing analytics": "Marketing Analytics",
        "looker studio": "Looker Studio",
        "data studio": "Looker Studio",
        "google tag manager": "Google Tag Manager",
        "gtm": "Google Tag Manager",
        "facebook pixel": "Meta Pixel",
        "meta pixel": "Meta Pixel",
        "ecommerce analytics": "eCommerce Analytics",
        "shopify analytics": "Shopify Analytics",
        "shopify": "Shopify",
        "multi-channel tracking": "Multi-Channel Attribution",
        "google ads": "Google Ads",
        "facebook ads": "Facebook Ads",
        "meta ads": "Meta Ads",
        "tiktok ads": "TikTok Ads",
        "linkedin ads": "LinkedIn Ads",
        "performance marketing": "Performance Marketing",
        "data-driven marketing": "Data-Driven Marketing",
        "digital analytics": "Digital Analytics",
        "ppc": "PPC",
        "saas": "SaaS",
        "ecommerce": "eCommerce",
        "affiliate marketing": "Affiliate Marketing",
        "martech": "MarTech",
        "bigquery": "BigQuery",
        "seo": "SEO",
        "digital marketing": "Digital Marketing",
        "supermetrics": "Marketing Tools",
        "windsor": "Windsor.ai",
    }
    seen: set[str] = set()
    tags: list[str] = []
    for kw in matched_keywords:
        tag = tag_map.get(kw)
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
        if len(tags) >= 5:
            break
    return tags


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_channel_relevance(ch: dict) -> dict:
    """
    Score a single channel dict deterministically.

    Returns a dict with keys matching the old LLM ScoringResult:
      relevance_score       float  0-30
      relevance_rationale   str
      niche_tags            list[str]
    Plus an extra key:
      keyword_score_raw     int  (raw points before composite weighting)
    """
    text = _build_text_blob(ch)

    # Disqualify immediately if negative keyword found
    for neg in NEGATIVE_KEYWORDS:
        if neg in text:
            return {
                "relevance_score": 0.0,
                "relevance_rationale": f"Disqualified: negative keyword '{neg}' found.",
                "niche_tags": [],
                "keyword_score_raw": 0,
            }

    keyword_pts, matched = _keyword_points(text)

    # Score of 0 → hard skip (no relevant keywords at all)
    if keyword_pts == 0:
        return {
            "relevance_score": 0.0,
            "relevance_rationale": "No Windsor.ai-relevant keywords found in channel content.",
            "niche_tags": [],
            "keyword_score_raw": 0,
        }

    upload_pts = _upload_freq_score(ch.get("upload_frequency_days", 0.0))
    view_pts = _view_ratio_score(
        ch.get("avg_views_per_video", 0.0),
        ch.get("subscriber_count", 0),
    )

    # Composite on a 0-50 raw scale, then scale to 0-30
    raw_composite = (keyword_pts * 0.6) + (upload_pts * 0.2) + (view_pts * 0.2)
    # keyword_pts max ≈ 50 (all high-value); raw_composite max ≈ 32; scale to 30
    relevance_score = round(min(30.0, raw_composite * (30.0 / 32.0)), 2)

    niche_tags = _infer_niche_tags(matched)

    # Human-readable rationale
    if keyword_pts >= 6:
        fit = "strong fit"
    elif keyword_pts >= 4:
        fit = "good fit"
    elif keyword_pts >= 2:
        fit = "moderate fit"
    else:
        fit = "weak fit"

    top_kws = ", ".join(matched[:4]) if matched else "general marketing"
    rationale = (
        f"{fit.capitalize()} for Windsor.ai — matched keywords: {top_kws}. "
        f"Upload frequency score: {upload_pts:.0f}/10, view ratio score: {view_pts:.1f}/10."
    )

    return {
        "relevance_score": relevance_score,
        "relevance_rationale": rationale,
        "niche_tags": niche_tags,
        "keyword_score_raw": keyword_pts,
    }
