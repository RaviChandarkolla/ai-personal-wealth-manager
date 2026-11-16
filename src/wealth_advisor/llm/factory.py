from typing import Any

from dotenv import load_dotenv

from ..config import get_settings
from .base import LLMClient
from .cache import CachedLLM
from .langchain_client import LangChainClient

load_dotenv()


def get_llm(cached: bool | None = None, trace: bool = True) -> LLMClient:
    """Build the configured provider client, optionally wrapped in the on-disk cache."""
    s = get_settings()
    use_cache = s.llm_cache_enabled if cached is None else cached
    chat: Any
    if s.llm_provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        chat = ChatAnthropic(model=s.llm_model, temperature=s.llm_temperature)
    elif s.llm_provider == "openai":
        from langchain_openai import ChatOpenAI

        chat = ChatOpenAI(
            model=s.llm_model, temperature=s.llm_temperature, use_responses_api=False
        )
    else:
        raise ValueError(f"unknown llm_provider: {s.llm_provider}")

    client: LLMClient = LangChainClient(chat, s.llm_model, trace=trace)
    return CachedLLM(client, s.llm_cache_dir) if use_cache else client

