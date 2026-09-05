from pathlib import Path

import pytest

from math_tutor.agent.runtime_factory import BoundedConversationEngine, build_tutoring_runtime
from math_tutor.application.provisioning import CreateLearner, CreateLearningPlan, ProvisioningService, RevokeAudioConsent, SessionLimits, StartLearningSession
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs
from math_tutor.infrastructure.dispatch import DispatchMetadata, VoiceBootstrapError
from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository


class Purger:
    def __init__(self): self.calls=[]
    def purge_consent_scope(self, consent_id, session_ids): self.calls.append((consent_id, session_ids))


PROVIDERS = {
    "STT_PROVIDER":"deepgram", "STT_MODEL":"nova-3", "STT_API_KEY":"stt-secret",
    "LLM_PROVIDER":"openai", "LLM_MODEL":"gpt-4o-mini-2024-07-18", "LLM_API_KEY":"llm-secret",
    "TTS_PROVIDER":"elevenlabs", "TTS_MODEL":"eleven_turbo_v2_5", "TTS_VOICE_ID":"voice", "TTS_API_KEY":"tts-secret",
}


def setup(tmp_path, *, consent=False):
    path=tmp_path/"bootstrap.db"; migrate(path)
    curriculum,_=load_curriculum_catalogs(Path("src/math_tutor/curricula/primary-math-v1.yaml"),Path("src/math_tutor/curricula/activity-templates-v1.yaml"))
    repo=SQLiteTutoringRepository(path); purger=Purger(); service=ProvisioningService(repo,curriculum,purger)
    service.create_learner(CreateLearner("learner-X", "Ana", 7))
    plan=service.create_learning_plan(CreateLearningPlan("plan-X", "learner-X", ("units-tens",), ("slow-pace",), SessionLimits(11, 5)))
    grant=service.grant_audio_consent("learner-X",retention_days=2) if consent else None
    started=service.start_learning_session(StartLearningSession("learner-X", grant.consent_id if grant else None))
    metadata=DispatchMetadata(started.tutoring_session_id,1,plan.plan_id,plan.version)
    return repo,service,purger,grant,started,metadata


@pytest.mark.parametrize("consent", [False, True])
def test_worker_reconstructs_exact_authorised_runtime_with_optional_consent(tmp_path, consent):
    repo,_,_,grant,started,metadata=setup(tmp_path,consent=consent)
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    assert runtime.bootstrap.learner.pseudonym == "Ana"
    assert runtime.bootstrap.plan.adaptations == ("slow-pace",)
    assert runtime.bootstrap.plan.limits == SessionLimits(11,5)
    assert runtime.bootstrap.session.authorised_objective_ids == ("units-tens",)
    assert runtime.bootstrap.clip_capture_enabled is consent


def test_revocation_disables_clips_without_ending_session(tmp_path):
    repo,service,purger,grant,started,metadata=setup(tmp_path,consent=True)
    service.revoke_audio_consent(RevokeAudioConsent("learner-X",grant.consent_id))
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    assert runtime.bootstrap.clip_capture_enabled is False
    assert runtime.bootstrap.session.can_continue is True
    assert purger.calls == [(grant.consent_id,(started.tutoring_session_id,))]


def test_worker_rejects_stale_dispatch_before_provider_validation(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    stale=DispatchMetadata(metadata.tutoring_session_id, 2, metadata.plan_id, metadata.expected_plan_version)
    with pytest.raises(VoiceBootstrapError):
        build_tutoring_runtime(metadata=stale,repository=repo,env={})


def test_composition_selects_reviewed_initial_activity_before_voice(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"))
    assert engine.initial_prompt
    assert repo.load_activity(metadata.tutoring_session_id,"activity-1").objective_id == "units-tens"
