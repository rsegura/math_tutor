from dataclasses import replace

from math_tutor.application.ports import (
    CommitDecision,
    MutationBatch,
    PersistedTutoringState,
    StoredCommandResult,
)
from math_tutor.application.results import CommandStatus
from math_tutor.application.service import EndSession, TutoringService
from math_tutor.application.session_runtime import SessionRuntime
from math_tutor.domain.learning import LearningPlan, LearningSession, PresentationProfile


def _state(version: int = 1) -> PersistedTutoringState:
    plan = LearningPlan(
        learner_id="learner-1",
        authorised_objective_ids=("units-tens",),
        active_objective_ids=("units-tens",),
        presentation=PresentationProfile.for_age(8),
    )
    return PersistedTutoringState(
        session=replace(LearningSession.start(session_id="session-1", plan=plan), version=version),
        profile_version=3,
    )


class Repository:
    def __init__(self, state=None):
        self.state = state or _state()
        self.batches: list[MutationBatch] = []
        self.results = {}
        self.fail = False

    def load_state(self, session_id):
        return self.state

    def load_command_result(self, command_id):
        return self.results.get(command_id)

    def load_activity(self, session_id, activity_id):
        return None

    def load_evidence(self, learner_id, objective_id):
        return ()

    def load_estimate(self, learner_id, objective_id):
        return None

    def commit_once(self, batch):
        if self.fail:
            raise OSError("storage unavailable")
        if batch.command_id in self.results:
            return CommitDecision.replayed(self.results[batch.command_id].result)
        if batch.expected_session_version != self.state.session.version:
            return CommitDecision.conflict("stale-session-version")
        if batch.expected_profile_version != self.state.profile_version:
            return CommitDecision.conflict("stale-profile-version")
        self.batches.append(batch)
        self.state = replace(self.state, session=batch.session or self.state.session)
        self.results[batch.command_id] = StoredCommandResult(batch.command_fingerprint, batch.result)
        return CommitDecision.committed(batch.result)


def _command(command_id="cmd-1", session_version=1, profile_version=3, generation_id="gen-1", reason="completed"):
    return EndSession(
        command_id=command_id,
        session_id="session-1",
        expected_session_version=session_version,
        expected_profile_version=profile_version,
        reason=reason,
        generation_id=generation_id,
    )


def test_mutation_rejects_stale_session_version_without_writing():
    repository = Repository()
    runtime = SessionRuntime(); runtime.start_generation("session-1")
    result = TutoringService(repository, runtime).end_session(
        _command(session_version=9)
    )
    assert result.status is CommandStatus.REJECTED
    assert result.reason == "stale-session-version"
    assert repository.batches == []


def test_mutation_rejects_stale_profile_version_without_writing():
    repository = Repository()
    runtime = SessionRuntime(); runtime.start_generation("session-1")
    result = TutoringService(repository, runtime).end_session(
        _command(profile_version=9)
    )
    assert result.status is CommandStatus.REJECTED
    assert result.reason == "stale-profile-version"
    assert repository.batches == []


def test_duplicate_command_returns_durable_result_without_second_write():
    repository = Repository()
    runtime = SessionRuntime(); runtime.start_generation("session-1")
    service = TutoringService(repository, runtime)
    first = service.end_session(_command())
    second = service.end_session(_command())
    assert first.status is CommandStatus.APPLIED
    assert second == replace(first, replayed=True)
    assert len(repository.batches) == 1


def test_persistence_failure_is_fail_closed_and_cancels_generation():
    repository = Repository()
    repository.fail = True
    runtime = SessionRuntime()
    generation = runtime.start_generation("session-1")
    result = TutoringService(repository, runtime).end_session(_command())
    assert result.status is CommandStatus.PERSISTENCE_FAILED
    assert generation.cancelled
    assert repository.state.session.ended is False


def test_mutation_from_superseded_generation_is_rejected():
    repository = Repository()
    runtime = SessionRuntime()
    stale = runtime.start_generation("session-1")
    runtime.start_generation("session-1")
    result = TutoringService(repository, runtime).end_session(
        _command(generation_id=stale.generation_id)
    )
    assert result.status is CommandStatus.REJECTED
    assert result.reason == "generation-not-active"
    assert repository.batches == []


def test_reused_command_id_with_different_arguments_is_rejected():
    repository = Repository()
    runtime = SessionRuntime(); runtime.start_generation("session-1")
    service = TutoringService(repository, runtime)
    first = service.end_session(_command(reason="completed"))
    runtime.start_generation("session-1")
    second = service.end_session(_command(reason="different-reason", generation_id="gen-2"))
    assert first.status is CommandStatus.APPLIED
    assert second.status is CommandStatus.REJECTED
    assert second.reason == "command-id-collision"


def test_generation_superseded_during_validation_cannot_cross_fence():
    repository = Repository()
    runtime = SessionRuntime()
    first = runtime.start_generation("session-1")
    original_load = repository.load_state

    def load_and_supersede(session_id):
        state = original_load(session_id)
        runtime.start_generation(session_id)
        return state

    repository.load_state = load_and_supersede
    result = TutoringService(repository, runtime).end_session(
        _command(generation_id=first.generation_id)
    )
    assert result.status is CommandStatus.REJECTED
    assert result.reason == "generation-not-active"
    assert repository.batches == []
    assert runtime.active_generation("session-1").generation_id == "gen-2"
