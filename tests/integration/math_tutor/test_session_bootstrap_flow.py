from pathlib import Path
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import sqlite3
import asyncio
from threading import Event
from unittest.mock import Mock

import pytest

from math_tutor.agent.runtime_factory import BoundedConversationEngine, build_tutoring_runtime
from math_tutor.application.provisioning import CreateLearner, CreateLearningPlan, ProvisioningService, RevokeAudioConsent, SessionLimits, StartLearningSession
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs
from math_tutor.infrastructure.dispatch import DispatchMetadata, VoiceBootstrapError
from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository
from math_tutor.infrastructure.persistence.repositories import _dump
from math_tutor.application.ports import ActivityProgress
from math_tutor.agent.voice_agent import HarnessVoiceAgent, VoiceTurn


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


@pytest.mark.asyncio
async def test_immediate_stop_uses_eager_registry_without_creating_provider(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    created=[]
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model_factory=lambda: created.append(True))
    engine._registry.execute=Mock(wraps=engine._registry.execute)
    decision=await engine.decide(VoiceTurn("turn-stop","Quiero parar",.99,Event()))
    assert created == []
    engine._registry.execute.assert_called_once()
    assert decision.terminal and decision.reason == "stop-requested"
    state=repo.load_state(metadata.tutoring_session_id)
    assert state.session.ended and state.session.end_reason == "stop-requested"


@pytest.mark.asyncio
async def test_lazy_model_factory_is_cached_for_normal_turns(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    created=[]
    class SocialModel:
        async def complete(self, **kwargs):
            return {"type":"reply","speech":"Vamos paso a paso.","speech_kind":"social"}
    def factory():
        created.append(True)
        return SocialModel()
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model_factory=factory)
    assert created == []
    await engine.decide(VoiceTurn("turn-1","No lo sé",.99,Event()))
    await engine.decide(VoiceTurn("turn-2","Todavía no",.99,Event()))
    assert created == [True]


@pytest.mark.asyncio
async def test_concurrent_lazy_initialization_creates_at_most_one_adapter(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    created=[]
    def factory():
        created.append(True)
        return SimpleModel()
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model_factory=factory)
    first,second=await asyncio.gather(engine._get_harness(),engine._get_harness())
    assert first is second
    assert created == [True]


@pytest.mark.asyncio
async def test_engine_close_is_noop_before_creation_and_idempotently_closes_created_adapter(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    class ClosableModel(SimpleModel):
        def __init__(self): self.closes=0
        async def aclose(self): self.closes += 1
    model=ClosableModel()
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model_factory=lambda:model)
    await engine.aclose()
    assert model.closes == 0
    await engine._get_harness()
    await engine.aclose(); await engine.aclose()
    assert model.closes == 1


@pytest.mark.asyncio
async def test_engine_retries_model_close_after_failure(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    class FlakyModel(SimpleModel):
        def __init__(self): self.closes=0
        async def aclose(self):
            self.closes += 1
            if self.closes == 1: raise RuntimeError("failed")
    model=FlakyModel()
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=model)
    with pytest.raises(RuntimeError): await engine.aclose()
    await engine.aclose()
    assert model.closes == 2


def test_worker_rejects_empty_active_objectives_before_provider_configuration(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    state=repo.load_state(metadata.tutoring_session_id)
    forged=replace(state.session,active_objective_ids=())
    with sqlite3.connect(repo.database) as db:
        db.execute("UPDATE learning_sessions SET session_json=? WHERE session_id=?",(_dump(forged),metadata.tutoring_session_id))
    with pytest.raises(VoiceBootstrapError,match="objective"):
        build_tutoring_runtime(metadata=metadata,repository=repo,env={})


def test_expired_duration_cap_ends_durably_before_model_or_activity(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    with sqlite3.connect(repo.database) as db:
        db.execute("UPDATE learning_sessions SET created_at=? WHERE session_id=?",((datetime.now(timezone.utc)-timedelta(minutes=20)).isoformat(),metadata.tutoring_session_id))
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),now=lambda:datetime.now(timezone.utc),model=SimpleModel())
    assert engine.startup_terminal_reason == "duration-cap-reached"
    assert repo.load_state(metadata.tutoring_session_id).session.ended
    assert repo.load_session_aggregate(metadata.tutoring_session_id).activities == ()


class SimpleModel:
    def complete(self,**kwargs): raise AssertionError("model must not run")


class CorrectAnswerModel:
    def __init__(self, activity): self.activity = activity
    def complete(self, **kwargs):
        return {
            "type": "tool",
            "name": "record_answer",
            "arguments": {
                "turn_id": kwargs["context"].current_turn.turn_id,
                "answer": {
                    "status": "evaluable",
                    "kind": self.activity.expected_answer.kind.value,
                    "values": dict(self.activity.expected_answer.values),
                },
            },
        }


@pytest.mark.asyncio
async def test_correct_answer_persists_and_speaks_next_canonical_activity(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    first_engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=SimpleModel())
    first=repo.load_activity(metadata.tutoring_session_id,"activity-1")
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=CorrectAnswerModel(first))

    decision=await engine.decide(VoiceTurn("turn-correct","respuesta",.9,Event()))

    aggregate=repo.load_session_aggregate(metadata.tutoring_session_id)
    selected=[event.activity_id for event in aggregate.events if event.kind=="activity-selected"]
    assert len(selected) == 2
    next_activity=repo.load_activity(metadata.tutoring_session_id,selected[-1])
    assert selected[-1] != "activity-1"
    assert decision.speech == f"Sí, esa respuesta es correcta. {next_activity.prompt_es}"


@pytest.mark.asyncio
async def test_retry_of_correct_turn_replays_the_same_follow_up_without_new_mutation(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=SimpleModel())
    first=repo.load_activity(metadata.tutoring_session_id,"activity-1")
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=CorrectAnswerModel(first))
    turn=VoiceTurn("retried-turn","respuesta",.9,Event())

    initial=await engine.decide(turn)
    replay=await engine.decide(turn)

    aggregate=repo.load_session_aggregate(metadata.tutoring_session_id)
    assert replay == initial
    assert len(aggregate.observations) == 1
    assert len([event for event in aggregate.events if event.kind=="activity-selected"]) == 2


@pytest.mark.asyncio
async def test_correct_answer_at_activity_cap_ends_without_creating_another_activity(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    with sqlite3.connect(repo.database) as db:
        db.execute("UPDATE provisioned_plans SET max_activities=1 WHERE plan_id=?",(metadata.plan_id,))
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    bootstrap_engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=SimpleModel())
    first=repo.load_activity(metadata.tutoring_session_id,"activity-1")
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=CorrectAnswerModel(first))

    decision=await engine.decide(VoiceTurn("last-turn","respuesta",.9,Event()))

    aggregate=repo.load_session_aggregate(metadata.tutoring_session_id)
    assert decision.terminal and decision.reason == "activity-cap-reached"
    assert len(aggregate.activities) == 1
    assert aggregate.session.ended


@pytest.mark.asyncio
async def test_completed_activity_cap_stops_before_next_model_call(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    with sqlite3.connect(repo.database) as db:
        db.execute("UPDATE provisioned_plans SET max_activities=1 WHERE plan_id=?",(metadata.plan_id,))
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=SimpleModel())
    with sqlite3.connect(repo.database) as db:
        db.execute("UPDATE activity_progress SET progress_json=? WHERE session_id=?",(_dump(ActivityProgress("activity-1",1,0,1,1)),metadata.tutoring_session_id))
    decision=await engine.decide(VoiceTurn("turn","uno",.9,Event()))
    assert decision.terminal and decision.reason == "activity-cap-reached"
    assert repo.load_state(metadata.tutoring_session_id).session.ended


@pytest.mark.asyncio
async def test_duration_rechecked_after_model_before_tool_mutation_or_speech(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    current=[runtime.bootstrap.session_started_at+timedelta(minutes=10)]
    class CrossingModel:
        def complete(self,**kwargs):
            current[0] += timedelta(minutes=2)
            return {"type":"tool","name":"give_hint","arguments":{}}
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),now=lambda:current[0],model=CrossingModel())
    decision=await engine.decide(VoiceTurn("turn","una pista",.9,Event()))
    assert decision.terminal and decision.reason == "duration-cap-reached"
    progress=repo.load_state(metadata.tutoring_session_id).progress_for("activity-1")
    assert progress.hints_used == 0


@pytest.mark.asyncio
async def test_slow_social_reply_is_suppressed_and_session_ends_durably(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    current=[runtime.bootstrap.session_started_at+timedelta(minutes=10)]
    class SlowSocialModel:
        def complete(self,**kwargs):
            current[0] += timedelta(minutes=2)
            return {"type":"reply","speech":"Vamos paso a paso.","speech_kind":"social"}
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),now=lambda:current[0],model=SlowSocialModel())
    decision=await engine.decide(VoiceTurn("turn","hola",.9,Event()))
    assert decision == __import__('math_tutor.agent.voice_agent',fromlist=['VoiceDecision']).VoiceDecision("La sesión ha terminado por hoy.",terminal=True,reason="duration-cap-reached")
    assert repo.load_state(metadata.tutoring_session_id).session.ended


@pytest.mark.asyncio
async def test_provider_error_returns_reviewed_terminal_response_and_stops_durably(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path); runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    class Broken:
        async def complete(self,**kwargs): raise RuntimeError("secret provider detail")
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=Broken())
    decision=await engine.decide(VoiceTurn("turn","cuatro",.9,Event()))
    assert decision.speech == "No puedo continuar ahora. Terminamos por hoy."
    assert decision.terminal and decision.reason == "llm-provider-unavailable"
    events=repo.load_session_aggregate(metadata.tutoring_session_id).events
    assert [(event.kind,event.detail) for event in events if event.kind=="session-stop-requested"] == [("session-stop-requested","llm-provider-unavailable")]


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["tts-first-audio-timeout", "tts-total-timeout", "tts-provider-error"])
async def test_tts_fail_safe_stops_durably_then_falls_back_and_closes_once(tmp_path, reason):
    repo,_,_,_,_,metadata=setup(tmp_path)
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=SimpleModel())
    order=[]
    class Fallback:
        def enqueue(self): order.append("fallback"); return "handle"
    class Closer:
        def trigger(self, value, handle=None): order.append(("close",value,handle))
    def stop(value):
        engine.force_stop(value)
        order.append(("stop",value))
    agent=HarnessVoiceAgent(instructions="bounded",initial_prompt=engine.initial_prompt,decide=engine.decide,cancel=engine.cancel,force_stop=stop,fallback_audio=Fallback())
    agent.bind_terminal_closer(Closer())
    await agent._on_tts_terminal(reason,"ignored")
    await agent._on_tts_terminal(reason,"ignored")
    pending=agent._tts_terminal_pending
    agent._tts_terminal_pending=None
    agent._closer.trigger(*pending)
    state=repo.load_state(metadata.tutoring_session_id)
    events=repo.load_session_aggregate(metadata.tutoring_session_id).events
    assert state.session.ended and not state.session.can_continue
    assert [(event.kind,event.detail) for event in events if event.kind=="session-stop-requested"] == [("session-stop-requested",reason)]
    assert order == [("stop",reason),"fallback",("close",reason,"handle")]
