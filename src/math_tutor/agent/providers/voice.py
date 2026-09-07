"""Construction boundary for configured speech providers."""

from __future__ import annotations

from math_tutor.agent.providers.settings import ProviderConfigError, ProviderSettings


def create_voice_providers(settings: ProviderSettings):
    """Build STT and TTS plugins without performing network access."""
    if settings.stt_provider == "deepgram":
        from livekit.plugins import deepgram

        stt = deepgram.STT(
            api_key=settings.stt_api_key,
            model=settings.stt_model,
            language="es",
        )
    elif settings.stt_provider == "openai":
        from livekit.plugins import openai as openai_plugin

        stt = openai_plugin.STT(
            api_key=settings.stt_api_key,
            model=settings.stt_model,
            language="es",
        )
    else:  # ProviderSettings normally prevents this; forged instances fail closed.
        raise ProviderConfigError("unsupported STT provider")

    if settings.tts_provider == "elevenlabs":
        from livekit.plugins import elevenlabs

        tts_kwargs = {
            "api_key": settings.tts_api_key,
            "model": settings.tts_model,
            "language": "es",
        }
        if settings.tts_voice_id is not None:
            tts_kwargs["voice_id"] = settings.tts_voice_id
        tts = elevenlabs.TTS(**tts_kwargs)
    elif settings.tts_provider == "openai":
        if settings.tts_voice_id is None:
            raise ProviderConfigError("TTS_VOICE_ID is required for openai TTS")
        from livekit.plugins import openai as openai_plugin

        tts = openai_plugin.TTS(
            api_key=settings.tts_api_key,
            model=settings.tts_model,
            voice=settings.tts_voice_id,
        )
    else:
        raise ProviderConfigError("unsupported TTS provider")
    return stt, tts


__all__ = ["create_voice_providers"]
