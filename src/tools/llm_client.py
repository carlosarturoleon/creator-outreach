from langchain_anthropic import ChatAnthropic
from src.config import settings


def get_llm(temperature: float = 0.3) -> ChatAnthropic:
    return ChatAnthropic(
        model=settings.claude_model,
        api_key=settings.anthropic_api_key,
        temperature=temperature,
        max_tokens=2048,
    )
