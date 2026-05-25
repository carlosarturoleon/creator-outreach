from pydantic import BaseModel, Field


class ScoringResult(BaseModel):
    """Structured output from Claude for the relevance scoring step."""

    relevance_score: float = Field(
        ge=0,
        le=30,
        description="Relevance score 0-30 for affiliate program fit",
    )
    relevance_rationale: str = Field(
        description="1-2 sentences explaining why this channel fits the affiliate program",
    )
    niche_tags: list[str] = Field(
        description="3-5 niche tags describing the channel (e.g. marketing analytics, SaaS, attribution)",
    )
