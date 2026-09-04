from dataclasses import replace

from math_tutor.application.ports import ActivityProgress, CommitDecision, PersistedTutoringState
from math_tutor.application.results import CommandStatus
from math_tutor.application.service import CanonicalHintResult, CommitHint, EndSession, RecordAnswer, RecordAnswerResult, SelectNextActivity
from math_tutor.domain.evidence import ObservationOutcome
from math_tutor.application.session_runtime import SessionRuntime
from math_tutor.domain.activities import StructuredAnswer
from math_tutor.domain.templates import ExpectedAnswerKind

from tests.unit.math_tutor.application.test_service import Repository, base, service


class ProgressRepository(Repository):
    def __init__(self, progress):
        super().__init__()
        self.state = replace(self.state, activity_progress=(progress,))
        self.bump_progress_on_commit = False

    def commit_once(self, batch):
        if self.bump_progress_on_commit:
            old = self.state.activity_progress[0]
            self.state = replace(self.state, activity_progress=(replace(old, version=old.version + 1),))
            self.bump_progress_on_commit = False
        current = self.state.progress_for(batch.expected_activity_progress[0].activity_id) if batch.expected_activity_progress else None
        for expected in batch.expected_activity_progress:
            if current is None or current.version != expected.version:
                return CommitDecision.conflict("stale-activity-progress")
        decision = super().commit_once(batch)
        if decision.outcome.value == "applied" and batch.activity_progress:
            self.state = replace(self.state, activity_progress=batch.activity_progress)
        return decision


def command(cls, **kwargs):
    return base(cls, **kwargs)


def test_stale_context_cannot_bypass_authoritative_attempt_cap():
    repo = ProgressRepository(ActivityProgress("activity-1", attempts_used=3, hints_used=0, difficulty=1, version=4))
    runtime = SessionRuntime(); runtime.start_generation("session-1")
    result = service(repo, runtime).record_answer(command(RecordAnswer, activity_id="activity-1", answer=StructuredAnswer.evaluable(ExpectedAnswerKind.INTEGER,{"value":2}), response_text="dos", stt_confidence=.9, assistance_level=0, observation_id="o", evidence_id=None, retain_evidence=False, reason_for_retention=None))
    assert result.status is CommandStatus.REJECTED
    assert result.reason == "attempt-cap-reached"
    assert not repo.batches


def test_hint_order_and_cap_are_checked_inside_atomic_fence():
    repo = ProgressRepository(ActivityProgress("activity-1", attempts_used=0, hints_used=1, difficulty=1, version=2))
    runtime = SessionRuntime(); runtime.start_generation("session-1")
    result = service(repo, runtime).commit_hint(command(CommitHint, activity_id="activity-1", hint_id="hint-1", hint_index=0))
    assert result.status is CommandStatus.REJECTED
    assert result.reason == "hint-not-next"


def test_one_answer_cannot_authorise_difficulty_adaptation():
    repo = ProgressRepository(ActivityProgress("activity-1", attempts_used=1, hints_used=0, difficulty=1, consecutive_correct=1, version=2))
    runtime = SessionRuntime(); runtime.start_generation("session-1")
    result = service(repo, runtime).select_next_activity(command(SelectNextActivity, source_activity_id="activity-1", objective_id="units-tens", template_id="place-value-units-count", seed=1, difficulty=2, activity_id="new"))
    assert result.status is CommandStatus.REJECTED
    assert result.reason == "insufficient-repeated-evidence"


def test_repeated_authoritative_evidence_allows_exactly_one_difficulty_step_and_versions_it():
    repo = ProgressRepository(ActivityProgress("activity-1", attempts_used=2, hints_used=0, difficulty=1, consecutive_correct=2, version=3))
    runtime = SessionRuntime(); runtime.start_generation("session-1")
    result = service(repo, runtime).select_next_activity(command(SelectNextActivity, source_activity_id="activity-1", objective_id="units-tens", template_id="place-value-units-count", seed=1, difficulty=2, activity_id="new"))
    assert result.status is CommandStatus.APPLIED
    assert repo.batches[-1].expected_activity_progress[0].version == 3
    assert repo.batches[-1].activity_progress[-1].version == 4
    assert repo.batches[-1].events[-1].detail.endswith("repeated-correct:1->2")


def test_low_confidence_answer_persists_only_not_evaluable_authoritative_outcome():
    repo = ProgressRepository(ActivityProgress("activity-1", 0, 0, 1))
    runtime = SessionRuntime(); runtime.start_generation("session-1")
    result = service(repo, runtime).record_answer(command(RecordAnswer, activity_id="activity-1", answer=StructuredAnswer.evaluable(ExpectedAnswerKind.INTEGER,{"value":2}), response_text="dos", stt_confidence=.2, assistance_level=0, observation_id="o", evidence_id=None, retain_evidence=False, reason_for_retention=None))
    assert isinstance(result.payload, RecordAnswerResult)
    assert result.payload.observation_outcome is ObservationOutcome.NOT_EVALUABLE
    assert repo.batches[-1].observations[0].outcome is ObservationOutcome.NOT_EVALUABLE
    assert repo.batches[-1].activity_progress[0].consecutive_correct == 0


def test_forged_assistance_counter_is_replaced_by_authoritative_hint_count():
    repo = ProgressRepository(ActivityProgress("activity-1", 0, 1, 1))
    runtime = SessionRuntime(); runtime.start_generation("session-1")
    result = service(repo, runtime).record_answer(command(RecordAnswer, activity_id="activity-1", answer=StructuredAnswer.evaluable(ExpectedAnswerKind.INTEGER,{"value":2}), response_text="dos", stt_confidence=.9, assistance_level=99, observation_id="o", evidence_id=None, retain_evidence=False, reason_for_retention=None))
    assert result.status is CommandStatus.APPLIED
    assert repo.batches[-1].observations[0].assistance_level == 1


def test_concurrent_progress_change_is_rejected_by_atomic_expectation():
    repo = ProgressRepository(ActivityProgress("activity-1", 0, 0, 1, version=2))
    repo.bump_progress_on_commit = True
    runtime = SessionRuntime(); runtime.start_generation("session-1")
    result = service(repo, runtime).record_answer(command(RecordAnswer, activity_id="activity-1", answer=StructuredAnswer.evaluable(ExpectedAnswerKind.INTEGER,{"value":2}), response_text="dos", stt_confidence=.9, assistance_level=0, observation_id="o", evidence_id=None, retain_evidence=False, reason_for_retention=None))
    assert result.status is CommandStatus.REJECTED
    assert result.reason == "stale-activity-progress"
    assert not repo.batches


def test_service_returns_only_configured_reviewed_hint_text():
    repo = ProgressRepository(ActivityProgress("activity-1", 0, 0, 1))
    runtime = SessionRuntime(); runtime.start_generation("session-1")
    tutor = service(repo, runtime)
    tutor._reviewed_hint_texts = {"hint-1": "Cuenta las unidades."}
    result = tutor.commit_hint(command(CommitHint, activity_id="activity-1", hint_id="hint-1", hint_index=0))
    assert result.payload == CanonicalHintResult("hint-1", "Cuenta las unidades.")


def test_stop_cancels_generation_before_failed_persistence():
    repo = Repository(); repo.fail = True
    runtime = SessionRuntime(); generation = runtime.start_generation("session-1")
    result = service(repo, runtime).stop_now(command(EndSession, reason="stop-requested"))
    assert generation.cancelled
    assert result.status is CommandStatus.PERSISTENCE_FAILED
