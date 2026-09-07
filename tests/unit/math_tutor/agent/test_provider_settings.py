import pytest

from math_tutor.agent.providers.settings import ProviderConfigError, ProviderSettings


def environment(**overrides: str) -> dict[str, str]:
    values = {
        "STT_PROVIDER": "deepgram",
        "STT_MODEL": "nova-3",
        "STT_API_KEY": "stt-super-secret",
        "LLM_PROVIDER": "openai",
        "LLM_MODEL": "gpt-4o-mini",
        "LLM_API_KEY": "llm-super-secret",
        "TTS_PROVIDER": "elevenlabs",
        "TTS_MODEL": "turbo",
        "TTS_VOICE_ID": "voice-1",
        "TTS_API_KEY": "tts-super-secret",
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    ("provider", "configured_url", "resolved_url"),
    [
        ("openai", "", None),
        ("openrouter", "", "https://openrouter.ai/api/v1"),
        ("openrouter", "https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1"),
        ("openrouter", "https://openrouter.ai/api/v1/", "https://openrouter.ai/api/v1"),
    ],
)
def test_resolves_exact_llm_provider_base_url_matrix(provider, configured_url, resolved_url):
    settings = ProviderSettings.from_environment(
        environment(LLM_PROVIDER=provider, LLM_BASE_URL=configured_url)
    )

    assert settings.llm_provider == provider
    assert settings.llm_base_url == resolved_url


@pytest.mark.parametrize(
    ("provider", "url"),
    [
        ("openai", "https://api.openai.com/v1"),
        ("openrouter", "http://openrouter.ai/api/v1"),
        ("openrouter", "https://key@openrouter.ai/api/v1"),
        ("openrouter", "https://openrouter.ai:443/api/v1"),
        ("openrouter", "https://api.openrouter.ai/api/v1"),
        ("openrouter", "https://openrouter.ai/api/v1/other"),
        ("openrouter", "https://openrouter.ai/api/v1?x=1"),
        ("openrouter", "https://openrouter.ai/api/v1#fragment"),
    ],
)
def test_rejects_noncanonical_llm_base_urls(provider, url):
    with pytest.raises(ProviderConfigError, match="LLM_BASE_URL"):
        ProviderSettings.from_environment(
            environment(LLM_PROVIDER=provider, LLM_BASE_URL=url)
        )


def test_rejects_unknown_llm_provider_without_exposing_secrets():
    env = environment(LLM_PROVIDER="future-provider")

    with pytest.raises(ProviderConfigError) as raised:
        ProviderSettings.from_environment(env)

    message = str(raised.value)
    assert "LLM_PROVIDER" in message
    assert all(env[name] not in message for name in ("STT_API_KEY", "LLM_API_KEY", "TTS_API_KEY"))


def test_strips_whitespace_and_hides_all_api_keys_from_repr():
    settings = ProviderSettings.from_environment(
        environment(
            STT_MODEL=" nova-3 ",
            STT_API_KEY=" stt-super-secret ",
            LLM_PROVIDER=" openrouter ",
            LLM_MODEL=" openai/gpt-4o-mini ",
            LLM_API_KEY=" llm-super-secret ",
            LLM_BASE_URL=" https://openrouter.ai/api/v1/ ",
            TTS_VOICE_ID=" voice-1 ",
            TTS_API_KEY=" tts-super-secret ",
        )
    )

    assert settings.stt_model == "nova-3"
    assert settings.llm_provider == "openrouter"
    assert settings.llm_model == "openai/gpt-4o-mini"
    assert settings.llm_base_url == "https://openrouter.ai/api/v1"
    assert settings.tts_voice_id == "voice-1"
    assert "super-secret" not in repr(settings)


def test_elevenlabs_voice_is_optional_and_empty_value_normalizes_to_none():
    settings = ProviderSettings.from_environment(environment(TTS_VOICE_ID="  "))

    assert settings.tts_voice_id is None


def test_openai_tts_requires_an_explicit_voice():
    with pytest.raises(ProviderConfigError, match="TTS_VOICE_ID is required for openai TTS"):
        ProviderSettings.from_environment(
            environment(TTS_PROVIDER="openai", TTS_VOICE_ID=" ")
        )


def test_validation_error_does_not_echo_a_secret_used_as_an_invalid_deadline():
    secret = "child-sensitive-secret"

    with pytest.raises(ProviderConfigError) as raised:
        ProviderSettings.from_environment(
            environment(LLM_FIRST_RESPONSE_SECONDS=secret)
        )

    assert secret not in str(raised.value)
