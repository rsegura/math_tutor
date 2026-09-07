"""Provider-neutral runtime configuration.

This module deliberately imports no vendor SDK.  It validates the complete
environment once so downstream factories never need to reread it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


class ProviderConfigError(ValueError):
    """A sanitized operator-facing provider configuration error."""


_SUPPORTED = {
    "STT_PROVIDER": frozenset({"deepgram", "openai"}),
    "LLM_PROVIDER": frozenset({"openai", "openrouter"}),
    "TTS_PROVIDER": frozenset({"elevenlabs", "openai"}),
}
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ProviderConfigError(f"{name} is required")
    return value.strip()


def _optional(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProviderConfigError(f"{name} must be text")
    return value.strip() or None


def _deadline(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        value = float(env.get(name, str(default)))
    except (TypeError, ValueError):
        raise ProviderConfigError(f"{name} must be numeric") from None
    if not 2 <= value <= 15:
        raise ProviderConfigError(f"{name} must be between 2 and 15 seconds")
    return value


def _resolve_llm_base_url(provider: str, configured: str | None) -> str | None:
    if provider == "openai":
        if configured is not None:
            raise ProviderConfigError("LLM_BASE_URL is not supported for openai")
        return None
    if provider == "openrouter":
        if configured is None or configured in {
            _OPENROUTER_BASE_URL,
            f"{_OPENROUTER_BASE_URL}/",
        }:
            return _OPENROUTER_BASE_URL
        raise ProviderConfigError("LLM_BASE_URL must be the canonical OpenRouter HTTPS endpoint")
    # Provider membership is validated first; retain a fail-closed guard.
    raise ProviderConfigError("unsupported LLM_PROVIDER")


@dataclass(frozen=True, slots=True)
class ProviderSettings:
    stt_provider: str
    stt_model: str
    stt_api_key: str = field(repr=False)
    llm_provider: str
    llm_model: str
    llm_api_key: str = field(repr=False)
    llm_base_url: str | None
    tts_provider: str
    tts_model: str
    tts_voice_id: str | None
    tts_api_key: str = field(repr=False)
    llm_first_response_seconds: float = 4.0
    llm_total_seconds: float = 10.0

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> "ProviderSettings":
        required_names = (
            "STT_PROVIDER",
            "STT_MODEL",
            "STT_API_KEY",
            "LLM_PROVIDER",
            "LLM_MODEL",
            "LLM_API_KEY",
            "TTS_PROVIDER",
            "TTS_MODEL",
            "TTS_API_KEY",
        )
        values = {name: _required(env, name) for name in required_names}
        for name, supported in _SUPPORTED.items():
            if values[name] not in supported:
                raise ProviderConfigError(f"unsupported {name}")

        voice_id = _optional(env, "TTS_VOICE_ID")
        if values["TTS_PROVIDER"] == "openai" and voice_id is None:
            raise ProviderConfigError("TTS_VOICE_ID is required for openai TTS")

        base_url = _resolve_llm_base_url(
            values["LLM_PROVIDER"], _optional(env, "LLM_BASE_URL")
        )
        first = _deadline(env, "LLM_FIRST_RESPONSE_SECONDS", 4.0)
        total = _deadline(env, "LLM_TOTAL_SECONDS", 10.0)
        if first > total:
            raise ProviderConfigError(
                "LLM first response deadline must not exceed total deadline"
            )
        return cls(
            stt_provider=values["STT_PROVIDER"],
            stt_model=values["STT_MODEL"],
            stt_api_key=values["STT_API_KEY"],
            llm_provider=values["LLM_PROVIDER"],
            llm_model=values["LLM_MODEL"],
            llm_api_key=values["LLM_API_KEY"],
            llm_base_url=base_url,
            tts_provider=values["TTS_PROVIDER"],
            tts_model=values["TTS_MODEL"],
            tts_voice_id=voice_id,
            tts_api_key=values["TTS_API_KEY"],
            llm_first_response_seconds=first,
            llm_total_seconds=total,
        )
