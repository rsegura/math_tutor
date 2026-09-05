from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from math_tutor.agent.runtime_factory import ProviderConfigError, ProviderSettings
from math_tutor.infrastructure.dispatch import DispatchMetadata, DispatchMetadataError


def test_dispatch_contains_only_opaque_ids_and_expected_versions():
    value = DispatchMetadata("sess_A8qf", 1, "plan_B7jk", 3)
    assert DispatchMetadata.parse(value.to_json(), "math-tutor-sess_A8qf") == value
    assert set(value.as_dict()) == {
        "tutoring_session_id", "expected_session_version", "plan_id", "expected_plan_version"
    }


def test_dispatch_rejects_extra_or_room_mismatched_metadata():
    with pytest.raises(DispatchMetadataError):
        DispatchMetadata.parse('{"tutoring_session_id":"s","expected_session_version":1,"plan_id":"p","expected_plan_version":1,"learner_id":"secret"}', "math-tutor-s")
    with pytest.raises(DispatchMetadataError):
        DispatchMetadata.parse(DispatchMetadata("s", 1, "p", 1).to_json(), "other-s")


def test_provider_configuration_is_explicit_and_has_no_fallback():
    with pytest.raises(ProviderConfigError):
        ProviderSettings.from_environment({})
    with pytest.raises(ProviderConfigError):
        ProviderSettings.from_environment({
            "STT_PROVIDER":"unknown", "STT_MODEL":"m", "STT_API_KEY":"k",
            "LLM_PROVIDER":"openai", "LLM_MODEL":"m", "LLM_API_KEY":"k",
            "TTS_PROVIDER":"elevenlabs", "TTS_MODEL":"m", "TTS_VOICE_ID":"v", "TTS_API_KEY":"k",
        })


def test_agent_image_and_make_target_run_real_worker():
    dockerfile = Path("docker/Dockerfile.agent").read_text()
    makefile = Path("Makefile").read_text()
    assert 'CMD ["python", "-m", "math_tutor.agent.worker", "start"]' in dockerfile
    assert "python -m math_tutor.agent.worker start" in makefile


def test_worker_routes_model_access_through_harness_agent():
    source = Path("src/math_tutor/agent/worker.py").read_text()
    assert "HarnessVoiceAgent" in source
    assert "SilentLLM" in source
    assert "agent=Agent(" not in source
