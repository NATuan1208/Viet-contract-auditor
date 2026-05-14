"""Shared LLM configuration for all pipeline agents and LightRAG.

Priority order:
1) OPENAI_API_KEY  -> provider=openai, default model=gpt-4o-mini
2) CEREBRAS_API_KEY -> provider=cerebras, default model=qwen-3-235b-a22b-instruct-2507

Optional overrides:
- AUDIT_LLM_MODEL: force model name
- OPENAI_BASE_URL: custom OpenAI-compatible endpoint
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from openai import AsyncOpenAI

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env", override=False)


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    api_key: str
    model: str
    base_url: str | None


def get_llm_settings() -> LLMSettings:
    """Resolve provider, credentials, and model from environment."""
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    cerebras_key = os.getenv("CEREBRAS_API_KEY", "").strip()
    custom_base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None
    model_override = (
        os.getenv("AUDIT_LLM_MODEL", "").strip()
        or os.getenv("OPENAI_MODEL", "").strip()
        or None
    )

    if openai_key:
        return LLMSettings(
            provider="openai",
            api_key=openai_key,
            model=model_override or "gpt-4o-mini",
            base_url=custom_base_url,
        )

    if cerebras_key:
        return LLMSettings(
            provider="cerebras",
            api_key=cerebras_key,
            model=model_override or "qwen-3-235b-a22b-instruct-2507",
            base_url=custom_base_url or "https://api.cerebras.ai/v1",
        )

    raise RuntimeError(
        "Missing LLM API key. Set OPENAI_API_KEY (recommended for gpt-4o-mini) "
        "or CEREBRAS_API_KEY."
    )


def create_async_llm_client() -> AsyncOpenAI:
    """Build AsyncOpenAI client from resolved settings."""
    settings = get_llm_settings()
    kwargs = {"api_key": settings.api_key}
    if settings.base_url:
        kwargs["base_url"] = settings.base_url
    return AsyncOpenAI(**kwargs)
