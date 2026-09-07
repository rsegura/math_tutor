from __future__ import annotations

import pytest

from math_tutor.agent.providers.settings import ProviderConfigError
from math_tutor.agent.providers.settings import ProviderSettings
from math_tutor.agent.providers.voice import create_voice_providers


def settings(*, stt_provider="deepgram", tts_provider="elevenlabs", voice_id=None):
    return ProviderSettings(
        stt_provider=stt_provider,
        stt_model="stt-model",
        stt_api_key="stt-secret",
        llm_provider="openai",
        llm_model="llm-model",
        llm_api_key="llm-secret",
        llm_base_url=None,
        tts_provider=tts_provider,
        tts_model="tts-model",
        tts_voice_id=voice_id,
        tts_api_key="tts-secret",
    )


def _capture(monkeypatch):
    from livekit.plugins import deepgram, elevenlabs, openai

    calls = {}

    def construct(kind):
        def factory(**kwargs):
            calls[kind] = kwargs
            return kind

        return factory

    monkeypatch.setattr(deepgram, "STT", construct("deepgram-stt"))
    monkeypatch.setattr(openai, "STT", construct("openai-stt"))
    monkeypatch.setattr(elevenlabs, "TTS", construct("elevenlabs-tts"))
    monkeypatch.setattr(openai, "TTS", construct("openai-tts"))
    return calls


def test_elevenlabs_omits_voice_id_when_selection_is_empty(monkeypatch):
    calls = _capture(monkeypatch)

    stt, tts = create_voice_providers(settings(voice_id=None))

    assert (stt, tts) == ("deepgram-stt", "elevenlabs-tts")
    assert calls["deepgram-stt"] == {
        "api_key": "stt-secret",
        "model": "stt-model",
        "language": "es",
    }
    assert calls["elevenlabs-tts"] == {
        "api_key": "tts-secret",
        "model": "tts-model",
        "language": "es",
    }


def test_elevenlabs_forwards_explicit_voice_id(monkeypatch):
    calls = _capture(monkeypatch)

    create_voice_providers(settings(voice_id="voice-123"))

    assert calls["elevenlabs-tts"]["voice_id"] == "voice-123"


def test_openai_tts_uses_explicit_voice_and_preserves_openai_stt(monkeypatch):
    calls = _capture(monkeypatch)

    stt, tts = create_voice_providers(
        settings(stt_provider="openai", tts_provider="openai", voice_id="alloy")
    )

    assert (stt, tts) == ("openai-stt", "openai-tts")
    assert calls["openai-stt"] == {
        "api_key": "stt-secret",
        "model": "stt-model",
        "language": "es",
    }
    assert calls["openai-tts"] == {
        "api_key": "tts-secret",
        "model": "tts-model",
        "voice": "alloy",
    }


def test_openai_tts_rejects_forged_settings_without_voice(monkeypatch):
    _capture(monkeypatch)

    with pytest.raises(
        ProviderConfigError, match="TTS_VOICE_ID is required for openai TTS"
    ):
        create_voice_providers(settings(tts_provider="openai", voice_id=None))
