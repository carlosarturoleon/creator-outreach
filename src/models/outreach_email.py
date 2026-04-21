from pydantic import BaseModel, Field


class EmailResult(BaseModel):
    """Structured output from Claude for the email generation step."""

    subject_line: str = Field(
        description="Personalized email subject line (not generic)",
    )
    email_body: str = Field(
        description="Outreach email body, under 200 words, natural tone",
    )
    personalization_hooks: list[str] = Field(
        description="Specific content pieces from the channel referenced in the email",
    )
