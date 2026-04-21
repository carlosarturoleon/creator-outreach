import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    youtube_api_key: str = os.getenv("YOUTUBE_API_KEY", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    claude_model: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    max_videos_to_sample: int = int(os.getenv("MAX_VIDEOS_TO_SAMPLE", "10"))
    output_dir: str = os.getenv("OUTPUT_DIR", "output")

    def validate(self) -> None:
        if not self.youtube_api_key:
            raise ValueError("YOUTUBE_API_KEY is required. Copy .env.example to .env and fill it in.")
        if not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required. Copy .env.example to .env and fill it in.")


settings = Config()
