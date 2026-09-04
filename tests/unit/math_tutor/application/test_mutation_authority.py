import asyncio
from dataclasses import fields
from pathlib import Path
from threading import Event, Lock

from math_tutor.application.ports import CommitDecision, PersistedTutoringState, StoredObservation
from math_tutor.application.results import CommandResult, CommandStatus
from math_tutor.application.service import (
    EndSession,
    ProposeEvidence,
    RecordAnswer,
    SelectNextActivity,
    TutoringService,
)
from math_tutor.application.session_runtime import SessionRuntime
from math_tutor.domain.activities import StructuredAnswer
from math_tutor.domain.evidence import Observation, TranscriptionReliabilityPolicy
from math_tutor.domain.learning import LearningPlan, LearningSession, PresentationProfile, ProgressionPolicy, AssistanceThreshold, CompetencyState
from math_tutor.domain.templates import ExpectedAnswerKind
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs


ROOT = Path(__file__).parents[4]


def _catalog():
    return load_curriculum_catalogs(
        ROOT / "src/math_tutor/curricula/primary-math-v1.yaml",
        ROOT / "src/math_tutor/curricula/activity-templates-v1.yaml",
    )[1]


def _policy():
    return ProgressionPolicy(tuple(AssistanceThreshold(state, 3) for state in (
        CompetencyState.EXPLORING, CompetencyState.WITH_INTENSIVE_HELP,
        CompetencyState.WITH_LIGHT_HELP, CompetencyState.INDEPENDENT,
        CompetencyState.GENERALIZED,
    )))


class Repo:
    def __init__(self):
        plan = LearningPlan("learner-1", ("units-tens",), ("units-tens",), PresentationProfile.for_age(8))
        self.state = PersistedTutoringState(LearningSession.start(session_id="session-1", plan=plan), 1)
        self.results = {}
        self.activities = {}
        self.observations = {}
        self.batches = []
        self.lock = Lock()
        self.entered = Event()
        self.release = Event(); self.release.set()

    def load_state(self, session_id): return self.state
    def load_command_result(self, command_id): return self.results.get(command_id)
    def load_activity(self, session_id, activity_id): return self.activities.get(activity_id)
    def load_observation(self, session_id, observation_id): return self.observations.get(observation_id)
    def load_evidence(self, learner_id, objective_id): return ()
    def load_estimate(self, learner_id, objective_id): return None
    def commit_once(self, batch):
        self.entered.set(); self.release.wait(3)
        with self.lock:
            prior = self.results.get(batch.command_id)
            if prior:
                if prior.command_fingerprint == batch.command_fingerprint:
                    return CommitDecision.replayed(prior.result, prior.command_fingerprint)
                return CommitDecision.collision(prior.result, prior.command_fingerprint)
            for expected in batch.expected_observations:
                stored = self.observations.get(expected.observation_id)
                if stored is None or stored.version != expected.version:
                    return CommitDecision.conflict("stale-observation-version")
            decision = CommitDecision.applied(batch.result, batch.command_fingerprint)
            from math_tutor.application.ports import StoredCommandResult
            self.results[batch.command_id] = StoredCommandResult(batch.command_fingerprint, batch.result)
            self.batches.append(batch)
            if batch.session: self.state = PersistedTutoringState(batch.session, self.state.profile_version)
            return decision


def _service(repo, runtime):
    return TutoringService(repo, runtime, _catalog(), TranscriptionReliabilityPolicy(.75), _policy())


def _base(cls, generation_id="gen-1", **kwargs):
    return cls(command_id="cmd-1", session_id="session-1", expected_session_version=1,
               expected_profile_version=1, generation_id=generation_id, **kwargs)


def test_policies_and_domain_objects_are_not_command_authority():
    assert "transcription_policy" not in {f.name for f in fields(RecordAnswer)}
    assert "progression_policy" not in {f.name for f in fields(__import__("math_tutor.application.service", fromlist=["ProposeProfileChange"]).ProposeProfileChange)}
    assert "activity" not in {f.name for f in fields(SelectNextActivity)}
    assert "observation" not in {f.name for f in fields(ProposeEvidence)}


def test_select_activity_resolves_reviewed_template_and_generates_it():
    repo = Repo(); runtime = SessionRuntime(); runtime.start_generation("session-1")
    command = _base(SelectNextActivity, activity_id="a-new", objective_id="units-tens",
                    template_id="place-value-units-count", seed=5, difficulty=1)
    result = _service(repo, runtime).select_next_activity(command)
    assert result.status is CommandStatus.APPLIED
    assert repo.batches[0].activities[0].activity.template_id == "place-value-units-count"


def test_propose_evidence_loads_canonical_observation_and_checks_activity():
    repo = Repo(); runtime = SessionRuntime(); runtime.start_generation("session-1")
    observation = Observation.from_answer(observation_id="obs-1", learner_id="learner-1",
        session_id="session-1", objective_id="units-tens", activity_id="missing",
        answer_outcome="correct", stt_confidence=.9, assistance_level=0,
        transcription_policy=TranscriptionReliabilityPolicy(.75))
    repo.observations["obs-1"] = StoredObservation(observation, version=1)
    result = _service(repo, runtime).propose_evidence(_base(ProposeEvidence,
        observation_id="obs-1", evidence_id="ev-1", interpretation="patrón",
        reason_for_retention="revisión"))
    assert result.status is CommandStatus.REJECTED
    assert result.reason == "observation-activity-not-found"


def test_command_id_collision_is_decided_atomically():
    repo = Repo(); runtime = SessionRuntime(); runtime.start_generation("session-1")
    first = _service(repo, runtime).end_session(_base(EndSession, reason="one"))
    runtime.start_generation("session-1")
    second = _service(repo, runtime).end_session(_base(EndSession, generation_id="gen-2", reason="two"))
    assert first.status is CommandStatus.APPLIED
    assert second.status is CommandStatus.REJECTED
    assert second.reason == "command-id-collision"


def test_service_fails_closed_when_atomic_commit_reports_racing_collision():
    class CollisionRepo(Repo):
        def load_command_result(self, command_id):
            return None  # another writer wins after this optimistic read

        def commit_once(self, batch):
            stored = CommandResult(batch.command_id, CommandStatus.APPLIED, "other")
            return CommitDecision.collision(stored, "different-fingerprint")

    repo = CollisionRepo(); runtime = SessionRuntime(); runtime.start_generation("session-1")
    result = _service(repo, runtime).end_session(_base(EndSession, reason="done"))
    assert result.status is CommandStatus.REJECTED
    assert result.reason == "command-id-collision"
    assert repo.state.session.ended is False


def test_fingerprint_is_stable_for_equivalent_mapping_order():
    left = _base(RecordAnswer, activity_id="a", answer=StructuredAnswer.evaluable(
        ExpectedAnswerKind.INTEGER, {"a": 1, "b": 2}), response_text=None,
        stt_confidence=.9, assistance_level=0, observation_id="o", evidence_id=None,
        retain_evidence=False, reason_for_retention=None)
    right = _base(RecordAnswer, activity_id="a", answer=StructuredAnswer.evaluable(
        ExpectedAnswerKind.INTEGER, {"b": 2, "a": 1}), response_text=None,
        stt_confidence=.9, assistance_level=0, observation_id="o", evidence_id=None,
        retain_evidence=False, reason_for_retention=None)
    assert TutoringService._fingerprint(left) == TutoringService._fingerprint(right)


def test_observation_snapshot_change_is_rejected_at_atomic_commit():
    class RacingRepo(Repo):
        def load_observation(self, session_id, observation_id):
            stored = super().load_observation(session_id, observation_id)
            self.observations[observation_id] = StoredObservation(stored.observation, stored.version + 1)
            return stored

    repo = RacingRepo(); runtime = SessionRuntime(); runtime.start_generation("session-1")
    observation = Observation.from_answer(observation_id="obs-1", learner_id="learner-1",
        session_id="session-1", objective_id="units-tens", activity_id="activity-1",
        answer_outcome="correct", stt_confidence=.9, assistance_level=0,
        transcription_policy=TranscriptionReliabilityPolicy(.75))
    repo.observations["obs-1"] = StoredObservation(observation, 1)
    from math_tutor.domain.activities import generate_activity
    repo.activities["activity-1"] = generate_activity(_catalog().template("place-value-units-count"), seed=1, difficulty=1)
    result = _service(repo, runtime).propose_evidence(_base(ProposeEvidence,
        observation_id="obs-1", evidence_id="ev-1", interpretation="patrón",
        reason_for_retention="revisión"))
    assert result.status is CommandStatus.REJECTED
    assert result.reason == "stale-observation-version"


def test_generation_cannot_start_while_atomic_commit_is_in_flight():
    async def scenario():
        repo = Repo(); repo.release.clear()
        runtime = SessionRuntime(); runtime.start_generation("session-1")
        mutation = asyncio.create_task(asyncio.to_thread(
            _service(repo, runtime).end_session, _base(EndSession, reason="done")))
        assert await asyncio.to_thread(repo.entered.wait, 1)
        started = asyncio.create_task(asyncio.to_thread(runtime.start_generation, "session-1"))
        await asyncio.sleep(.05)
        assert not started.done()
        repo.release.set()
        result = await mutation
        generation = await started
        assert result.status is CommandStatus.APPLIED
        assert generation.generation_id == "gen-2"
    asyncio.run(scenario())


def test_persistence_failure_releases_generation_barrier():
    class FailingRepo(Repo):
        def commit_once(self, batch): raise OSError("down")
    repo = FailingRepo(); runtime = SessionRuntime(); runtime.start_generation("session-1")
    result = _service(repo, runtime).end_session(_base(EndSession, reason="done"))
    generation = runtime.start_generation("session-1")
    assert result.status is CommandStatus.PERSISTENCE_FAILED
    assert generation.generation_id == "gen-2"
