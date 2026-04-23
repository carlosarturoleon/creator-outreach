import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _load_api_keys() -> list[str]:
    """Return all YouTube API keys (primary + extras), deduped, non-empty."""
    primary = os.getenv("YOUTUBE_API_KEY", "").strip()
    extras_raw = os.getenv("YOUTUBE_API_KEYS_EXTRA", "").strip()
    extras = [k.strip() for k in extras_raw.split(",") if k.strip()]
    seen = set()
    keys = []
    for k in [primary] + extras:
        if k and k not in seen:
            seen.add(k)
            keys.append(k)
    return keys


@dataclass(frozen=True)
class Config:
    youtube_api_key: str = os.getenv("YOUTUBE_API_KEY", "")
    youtube_api_keys: list = field(default_factory=_load_api_keys)
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    claude_model: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    max_videos_to_sample: int = int(os.getenv("MAX_VIDEOS_TO_SAMPLE", "10"))
    output_dir: str = os.getenv("OUTPUT_DIR", "output")
    # SMTP / email sending
    smtp_host: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    email_from: str = os.getenv("EMAIL_FROM", "")
    email_test_override: str = os.getenv("EMAIL_TEST_OVERRIDE", "")

    def validate(self) -> None:
        if not self.youtube_api_key:
            raise ValueError("YOUTUBE_API_KEY is required. Copy .env.example to .env and fill it in.")
        if not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required. Copy .env.example to .env and fill it in.")


settings = Config()
