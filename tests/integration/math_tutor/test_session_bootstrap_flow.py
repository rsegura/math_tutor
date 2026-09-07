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
from math_tutor.application.results import CommandResult, CommandStatus
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
async def test_help_turn_commits_one_reviewed_hint_without_observation_or_model(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    created=[]
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model_factory=lambda:created.append(True))

    decision=await engine.decide(VoiceTurn("help-1","No lo entiendo",.99,Event()))

    aggregate=repo.load_session_aggregate(metadata.tutoring_session_id)
    progress=next(item for item in aggregate.activity_progress if item.activity_id == "activity-1")
    activity=repo.load_activity(metadata.tutoring_session_id,"activity-1")
    receipt=repo.load_support_receipt(metadata.tutoring_session_id,"help-1")
    assert created == []
    assert aggregate.observations == ()
    assert progress.hints_used == 1 and progress.attempts_used == 0
    assert decision.speech == receipt.speech
    assert decision.speech.endswith(activity.prompt_es)
    assert receipt.action == "hint"
    with sqlite3.connect(repo.database) as db:
        columns={row[1] for row in db.execute("PRAGMA table_info(learner_support_receipts)")}
    assert "transcript" not in columns and "text" not in columns


@pytest.mark.asyncio
async def test_replayed_help_turn_returns_exact_receipt_and_does_not_consume_second_hint(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=SimpleModel())
    turn=VoiceTurn("same-help","Repítelo",.99,Event())

    first=await engine.decide(turn)
    replay=await engine.decide(turn)

    aggregate=repo.load_session_aggregate(metadata.tutoring_session_id)
    progress=next(item for item in aggregate.activity_progress if item.activity_id == "activity-1")
    assert replay == first
    assert progress.hints_used == 1
    with sqlite3.connect(repo.database) as db:
        assert db.execute("SELECT COUNT(*) FROM learner_support_receipts WHERE session_id=? AND turn_id=?",(metadata.tutoring_session_id,"same-help")).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_stop_preempts_a_support_receipt_with_the_same_turn_id(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=SimpleModel())
    await engine.decide(VoiceTurn("reused","ayuda",.99,Event()))

    decision=await engine.decide(VoiceTurn("reused","quiero parar",.99,Event()))

    assert decision.terminal and decision.reason == "stop-requested"
    assert repo.load_state(metadata.tutoring_session_id).session.ended


@pytest.mark.asyncio
async def test_elapsed_cap_preempts_low_confidence_and_support_receipt_replay(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    current=[datetime.now(timezone.utc)]
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=SimpleModel(),now=lambda:current[0])
    await engine.decide(VoiceTurn("reused","ayuda",.99,Event()))
    current[0] += timedelta(minutes=12)

    decision=await engine.decide(VoiceTurn("reused","ayuda",.2,Event()))

    assert decision.terminal and decision.reason == "duration-cap-reached"


@pytest.mark.asyncio
async def test_activity_cap_preempts_low_confidence_and_support_receipt_replay(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=SimpleModel())
    await engine.decide(VoiceTurn("reused","ayuda",.99,Event()))
    engine._bootstrap=replace(engine._bootstrap,plan=replace(engine._bootstrap.plan,limits=SessionLimits(11,1)))
    progress=repo.load_state(metadata.tutoring_session_id).progress_for("activity-1")
    completed=replace(progress,attempts_used=1,version=progress.version+1)
    with sqlite3.connect(repo.database) as db:
        db.execute("UPDATE activity_progress SET progress_json=?,version=? WHERE session_id=? AND activity_id=?",(_dump(completed),completed.version,metadata.tutoring_session_id,"activity-1"))

    decision=await engine.decide(VoiceTurn("reused","ayuda",.2,Event()))

    assert decision.terminal and decision.reason == "activity-cap-reached"


@pytest.mark.asyncio
async def test_ended_session_preempts_low_confidence_and_support_receipt_replay(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=SimpleModel())
    await engine.decide(VoiceTurn("reused","ayuda",.99,Event()))
    engine.force_stop("therapist-ended")

    decision=await engine.decide(VoiceTurn("reused","ayuda",.2,Event()))

    assert decision.terminal and decision.reason == "therapist-ended"


@pytest.mark.asyncio
async def test_normal_low_confidence_turn_confirms_without_model_or_observation(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    created=[]
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model_factory=lambda:created.append(True))

    decision=await engine.decide(VoiceTurn("low","ayuda",.2,Event()))

    assert decision.needs_confirmation and decision.reason == "low-stt-confidence"
    assert created == []
    assert repo.load_session_aggregate(metadata.tutoring_session_id).observations == ()


@pytest.mark.asyncio
async def test_ended_and_elapsed_caps_preempt_low_confidence(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    current=[datetime.now(timezone.utc)]
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=SimpleModel(),now=lambda:current[0])
    current[0] += timedelta(minutes=12)
    elapsed=await engine.decide(VoiceTurn("low-duration","ayuda",.2,Event()))
    ended=await engine.decide(VoiceTurn("low-ended","ayuda",.2,Event()))
    assert elapsed.terminal and elapsed.reason == "duration-cap-reached"
    assert ended.terminal and ended.reason == "duration-cap-reached"


@pytest.mark.asyncio
async def test_duration_cap_preempts_correct_turn_replay_without_new_follow_up(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    current=[datetime.now(timezone.utc)]
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=SimpleModel(),now=lambda:current[0])
    activity=repo.load_activity(metadata.tutoring_session_id,"activity-1")
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=CorrectAnswerModel(activity),now=lambda:current[0])
    turn=VoiceTurn("correct-replay-cap","respuesta",.9,Event())
    await engine.decide(turn)
    before=len(repo.load_session_aggregate(metadata.tutoring_session_id).activities)
    current[0] += timedelta(minutes=12)

    replay=await engine.decide(turn)

    aggregate=repo.load_session_aggregate(metadata.tutoring_session_id)
    assert replay.terminal and replay.reason == "duration-cap-reached"
    assert len(aggregate.activities) == before
    assert aggregate.session.ended


@pytest.mark.asyncio
async def test_activity_cap_preempts_correct_turn_replay_without_new_follow_up(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=SimpleModel())
    activity=repo.load_activity(metadata.tutoring_session_id,"activity-1")
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=CorrectAnswerModel(activity))
    turn=VoiceTurn("correct-replay-activity-cap","respuesta",.9,Event())
    await engine.decide(turn)
    before=len(repo.load_session_aggregate(metadata.tutoring_session_id).activities)
    engine._bootstrap=replace(engine._bootstrap,plan=replace(engine._bootstrap.plan,limits=SessionLimits(11,1)))

    replay=await engine.decide(turn)

    aggregate=repo.load_session_aggregate(metadata.tutoring_session_id)
    assert replay.terminal and replay.reason == "activity-cap-reached"
    assert len(aggregate.activities) == before
    assert aggregate.session.ended


@pytest.mark.asyncio
async def test_exhausted_help_repeats_prompt_without_progress_or_competence_mutation(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=SimpleModel())
    activity=repo.load_activity(metadata.tutoring_session_id,"activity-1")
    for index in range(len(activity.hint_ids)):
        await engine.decide(VoiceTurn(f"hint-{index}","ayúdame",.99,Event()))
    before=repo.load_session_aggregate(metadata.tutoring_session_id)

    decision=await engine.decide(VoiceTurn("exhausted","otra vez",.99,Event()))

    after=repo.load_session_aggregate(metadata.tutoring_session_id)
    receipt=repo.load_support_receipt(metadata.tutoring_session_id,"exhausted")
    assert decision.speech == activity.prompt_es
    assert receipt.action == "repeat"
    assert after.activity_progress == before.activity_progress
    assert after.estimates == before.estimates
    assert len(after.activities) == len(before.activities)


@pytest.mark.asyncio
async def test_support_receipt_failure_rolls_back_hint_progress_event_and_command(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=SimpleModel())
    with sqlite3.connect(repo.database) as db:
        db.execute("CREATE TRIGGER reject_support BEFORE INSERT ON learner_support_receipts BEGIN SELECT RAISE(ABORT,'test failure'); END")
    with pytest.raises(RuntimeError,match="learner support rejected"):
        await engine.decide(VoiceTurn("atomic-failure","ayuda",.99,Event()))
    aggregate=repo.load_session_aggregate(metadata.tutoring_session_id)
    progress=next(item for item in aggregate.activity_progress if item.activity_id == "activity-1")
    assert progress.hints_used == 0
    assert not any(event.kind == "hint-committed" for event in aggregate.events)
    assert repo.load_command_result(f"support:{metadata.tutoring_session_id}:atomic-failure") is None


@pytest.mark.asyncio
async def test_concurrent_same_help_turn_returns_exact_winner_and_one_hint(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    first=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=SimpleModel())
    second=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=SimpleModel())
    turn=VoiceTurn("racing-help","necesito ayuda",.99,Event())

    decisions=await asyncio.gather(
        asyncio.to_thread(lambda: asyncio.run(first.decide(turn))),
        asyncio.to_thread(lambda: asyncio.run(second.decide(turn))),
    )

    aggregate=repo.load_session_aggregate(metadata.tutoring_session_id)
    progress=next(item for item in aggregate.activity_progress if item.activity_id == "activity-1")
    assert decisions[0] == decisions[1]
    assert progress.hints_used == 1


@pytest.mark.asyncio
async def test_generation_race_loser_waits_boundedly_for_exact_support_receipt(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    winner=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=SimpleModel())
    loser=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=SimpleModel())
    turn=VoiceTurn("forced-window","ayuda",.99,Event())
    first_poll=asyncio.Event()
    loop=asyncio.get_running_loop()
    original_load=repo.load_support_receipt

    def delayed_load(session_id,turn_id):
        receipt=original_load(session_id,turn_id)
        if receipt is None:
            loop.call_soon_threadsafe(first_poll.set)
        return receipt

    repo.load_support_receipt=delayed_load
    loser._service.support_learner=lambda command:CommandResult(command.command_id,CommandStatus.REJECTED,"generation-not-active")
    losing_task=asyncio.create_task(loser.decide(turn))
    await asyncio.wait_for(first_poll.wait(),1)
    winning=await winner.decide(turn)
    losing=await asyncio.wait_for(losing_task,1)

    assert losing == winning
    progress=repo.load_state(metadata.tutoring_session_id).progress_for("activity-1")
    assert progress.hints_used == 1


@pytest.mark.asyncio
async def test_missing_race_winner_uses_a_bounded_number_of_receipt_reads(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path)
    runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=SimpleModel())
    reads=[]
    engine._service.support_learner=lambda command:CommandResult(command.command_id,CommandStatus.REJECTED,"generation-not-active")
    repo.load_support_receipt=lambda session_id,turn_id:reads.append((session_id,turn_id))

    with pytest.raises(RuntimeError,match="generation-not-active"):
        await asyncio.wait_for(engine.decide(VoiceTurn("no-winner","ayuda",.99,Event())),1)

    assert len(reads) == 8


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
    current_prompt=engine.initial_prompt
    decisions=[]
    for index in range(3):
        decisions.append(await engine.decide(VoiceTurn(f"turn-{index}","cuatro",.9,Event())))
    assert all(not item.terminal for item in decisions[:2])
    assert all(current_prompt in item.speech for item in decisions[:2])
    assert repo.load_session_aggregate(metadata.tutoring_session_id).observations == ()
    assert decisions[2].speech == "No puedo continuar ahora. Terminamos por hoy."
    assert decisions[2].terminal and decisions[2].reason == "llm-provider-unavailable"
    events=repo.load_session_aggregate(metadata.tutoring_session_id).events
    assert [(event.kind,event.detail) for event in events if event.kind=="session-stop-requested"] == [("session-stop-requested","llm-provider-unavailable")]


@pytest.mark.asyncio
async def test_success_resets_llm_failure_counter_and_help_preserves_it(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path); runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    class Sequence:
        def __init__(self): self.values=iter([RuntimeError("secret"), {"type":"reply","speech":"Vamos paso a paso.","speech_kind":"social"}, RuntimeError("secret"), RuntimeError("secret"), RuntimeError("secret")])
        async def complete(self,**kwargs):
            value=next(self.values)
            if isinstance(value,Exception): raise value
            return value
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=Sequence())
    first=await engine.decide(VoiceTurn("f1","respuesta",.9,Event()))
    success=await engine.decide(VoiceTurn("ok","respuesta",.9,Event()))
    second=await engine.decide(VoiceTurn("f2","respuesta",.9,Event()))
    help_turn=await engine.decide(VoiceTurn("help","no lo entiendo",.9,Event()))
    third=await engine.decide(VoiceTurn("f3","respuesta",.9,Event()))
    terminal=await engine.decide(VoiceTurn("f4","respuesta",.9,Event()))
    assert not first.terminal and success.speech == "Vamos paso a paso."
    assert not second.terminal and help_turn.reason.startswith("learner-support-") and not third.terminal
    assert terminal.terminal and terminal.reason == "llm-provider-unavailable"


@pytest.mark.asyncio
async def test_llm_failure_log_is_allowlisted_and_has_no_exception_chain(tmp_path, caplog):
    from math_tutor.harness.model import ProviderUpstreamUnavailable
    repo,_,_,_,_,metadata=setup(tmp_path); runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    class Broken:
        async def complete(self,**kwargs):
            try: raise RuntimeError("api-key transcript response-body child-name")
            except RuntimeError: raise ProviderUpstreamUnavailable() from None
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=Broken())
    with caplog.at_level("WARNING"):
        await engine.decide(VoiceTurn("turn-secret","private transcript",.9,Event()))
    record=next(record for record in caplog.records if record.getMessage()=="llm_turn_failed")
    assert record.failure_code == "provider-upstream-unavailable"
    assert record.provider == runtime.providers.llm_provider
    assert record.model == runtime.providers.llm_model
    assert record.session_id == metadata.tutoring_session_id
    assert record.consecutive_count == 1
    assert record.exc_info is False
    rendered=" ".join(str(value) for value in record.__dict__.values())
    assert "api-key" not in rendered and "private transcript" not in rendered and "response-body" not in rendered


@pytest.mark.asyncio
async def test_turn_epochs_are_monotonic_and_stale_completions_are_neutral(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path); runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=SimpleModel())
    first,second=await asyncio.gather(engine._begin_llm_turn(),engine._begin_llm_turn())
    assert (first,second) == (1,2)
    assert await engine._llm_failed(first) is None
    assert await engine._llm_succeeded(first) is False
    assert await engine._llm_failed(second) == 1
    assert await engine._llm_succeeded(second) is True
    assert engine._consecutive_llm_failures == 0


@pytest.mark.asyncio
async def test_cancelled_llm_turn_does_not_increment_failure_counter(tmp_path):
    repo,_,_,_,_,metadata=setup(tmp_path); runtime=build_tutoring_runtime(metadata=metadata,repository=repo,env=PROVIDERS)
    entered=asyncio.Event()
    class Blocked:
        async def complete(self,**kwargs):
            entered.set()
            await asyncio.Event().wait()
    engine=BoundedConversationEngine(repository=repo,runtime=runtime,curricula_dir=Path("src/math_tutor/curricula"),model=Blocked())
    task=asyncio.create_task(engine.decide(VoiceTurn("cancelled","respuesta",.9,Event())))
    await entered.wait(); task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert engine._consecutive_llm_failures == 0


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
