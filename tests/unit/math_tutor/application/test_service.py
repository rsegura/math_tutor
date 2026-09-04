from dataclasses import replace

from math_tutor.application.ports import CommitDecision, MutationBatch, PersistedTutoringState, StoredCommandResult
from math_tutor.application.results import CommandStatus
from math_tutor.application.service import (
    CommitHint,
    ProposeEvidence,
    ProposeProfileChange,
    RecordAnswer,
    SelectNextActivity,
    TutoringService,
)
from math_tutor.application.session_runtime import SessionRuntime
from math_tutor.domain.activities import Activity, StructuredAnswer
from math_tutor.domain.evidence import EvidenceRecord, Observation, ObservationOutcome, TranscriptionReliabilityPolicy
from math_tutor.domain.learning import (
    AssistanceThreshold, CompetencyState, LearningPlan, LearningSession,
    PresentationProfile, ProgressionPolicy, SkillEstimate,
)
from math_tutor.domain.templates import ExpectedAnswerKind


def activity(objective_id="units-tens"):
    return Activity(
        template_id="template-1", objective_id=objective_id, difficulty=1,
        prompt_es="¿Cuántas unidades?", parameters={"number": 12},
        expected_answer=StructuredAnswer.evaluable(ExpectedAnswerKind.INTEGER, {"value": 2}),
        error_pattern_ids=("wrong-place",), hint_ids=("hint-1", "hint-2"),
    )


class Repository:
    def __init__(self):
        plan = LearningPlan("learner-1", ("units-tens",), ("units-tens",), PresentationProfile.for_age(8))
        self.state = PersistedTutoringState(LearningSession.start(session_id="session-1", plan=plan), 1)
        self.activities = {"activity-1": activity()}
        self.batches = []
        self.results = {}
        self.fail = False
        self.evidence = ()
        self.estimate = None

    def load_state(self, session_id): return self.state
    def load_command_result(self, command_id): return self.results.get(command_id)
    def load_activity(self, session_id, activity_id): return self.activities.get(activity_id)
    def load_evidence(self, learner_id, objective_id): return self.evidence
    def load_estimate(self, learner_id, objective_id): return self.estimate
    def commit_once(self, batch):
        if self.fail: raise OSError("storage unavailable")
        if batch.command_id in self.results: return CommitDecision.replayed(self.results[batch.command_id].result)
        self.batches.append(batch); self.results[batch.command_id] = StoredCommandResult(batch.command_fingerprint, batch.result)
        if batch.session: self.state = replace(self.state, session=batch.session)
        return CommitDecision.committed(batch.result)


def base(cls, **kwargs):
    values = dict(command_id="cmd-1", session_id="session-1", expected_session_version=1, expected_profile_version=1, generation_id="gen-1")
    values.update(kwargs)
    return cls(**values)


def test_record_answer_atomically_writes_observation_evidence_and_event():
    repository = Repository()
    runtime = SessionRuntime(); runtime.start_generation("session-1")
    result = TutoringService(repository, runtime).record_answer(base(
        RecordAnswer, activity_id="activity-1",
        answer=StructuredAnswer.evaluable(ExpectedAnswerKind.INTEGER, {"value": 2}),
        response_text="dos", stt_confidence=.95, assistance_level=0,
        observation_id="obs-1", evidence_id="evidence-1", retain_evidence=True,
        reason_for_retention="first-independent-success",
        transcription_policy=TranscriptionReliabilityPolicy(.75),
    ))
    batch = repository.batches[0]
    assert result.status is CommandStatus.APPLIED
    assert batch.observations[0].outcome is ObservationOutcome.CORRECT
    assert batch.evidence[0].observation is batch.observations[0]
    assert batch.events[0].kind == "answer-recorded"


def test_record_answer_rejects_activity_outside_authorised_objectives():
    repository = Repository(); repository.activities["activity-2"] = activity("not-authorised")
    runtime = SessionRuntime(); runtime.start_generation("session-1")
    result = TutoringService(repository, runtime).record_answer(base(
        RecordAnswer, activity_id="activity-2", answer=StructuredAnswer.not_evaluable(),
        response_text=None, stt_confidence=.9, assistance_level=0,
        observation_id="obs-2", evidence_id=None, retain_evidence=False,
        reason_for_retention=None, transcription_policy=TranscriptionReliabilityPolicy(.75),
    ))
    assert result.status is CommandStatus.REJECTED
    assert result.reason == "objective-not-authorised"
    assert repository.batches == []


def test_select_next_activity_rejects_unauthorised_objective():
    repository = Repository()
    runtime = SessionRuntime(); runtime.start_generation("session-1")
    result = TutoringService(repository, runtime).select_next_activity(base(
        SelectNextActivity, activity=activity("not-authorised"), activity_id="new-activity"
    ))
    assert result.status is CommandStatus.REJECTED


def test_commit_hint_records_only_the_next_reviewed_hint():
    repository = Repository()
    runtime = SessionRuntime(); runtime.start_generation("session-1")
    result = TutoringService(repository, runtime).commit_hint(base(
        CommitHint, activity_id="activity-1", hint_id="hint-1", hint_index=0
    ))
    assert result.status is CommandStatus.APPLIED
    assert repository.batches[0].events[0].kind == "hint-committed"


def test_propose_evidence_keeps_interpretation_provisional():
    repository = Repository()
    runtime = SessionRuntime(); runtime.start_generation("session-1")
    answer = base(
        RecordAnswer, activity_id="activity-1",
        answer=StructuredAnswer.evaluable(ExpectedAnswerKind.INTEGER, {"value": 3}),
        response_text="tres", stt_confidence=.95, assistance_level=1,
        observation_id="obs-1", evidence_id=None, retain_evidence=False,
        reason_for_retention=None, transcription_policy=TranscriptionReliabilityPolicy(.75),
    )
    TutoringService(repository, runtime).record_answer(answer)
    repository.state = replace(repository.state, session=replace(repository.state.session, version=2))
    runtime.start_generation("session-1")
    result = TutoringService(repository, runtime).propose_evidence(base(
        ProposeEvidence, command_id="cmd-2", expected_session_version=2, generation_id="gen-2",
        observation=repository.batches[0].observations[0], evidence_id="evidence-1",
        interpretation="posible confusión de posición", reason_for_retention="repeated-error",
    ))
    assert result.status is CommandStatus.APPLIED
    assert repository.batches[-1].evidence[0].current_interpretation == "posible confusión de posición"


def test_record_answer_reports_no_success_when_atomic_persistence_fails():
    repository = Repository(); repository.fail = True
    runtime = SessionRuntime(); runtime.start_generation("session-1")
    result = TutoringService(repository, runtime).record_answer(base(
        RecordAnswer, activity_id="activity-1",
        answer=StructuredAnswer.evaluable(ExpectedAnswerKind.INTEGER, {"value": 2}),
        response_text="dos", stt_confidence=.95, assistance_level=0,
        observation_id="obs-1", evidence_id="evidence-1", retain_evidence=True,
        reason_for_retention="first-independent-success",
        transcription_policy=TranscriptionReliabilityPolicy(.75),
    ))
    assert result.status is CommandStatus.PERSISTENCE_FAILED
    assert repository.batches == []


def test_invalid_observation_is_rejected_at_mutation_fence():
    repository = Repository()
    runtime = SessionRuntime(); runtime.start_generation("session-1")
    result = TutoringService(repository, runtime).record_answer(base(
        RecordAnswer, activity_id="activity-1",
        answer=StructuredAnswer.evaluable(ExpectedAnswerKind.INTEGER, {"value": 2}),
        response_text="dos", stt_confidence=2.0, assistance_level=0,
        observation_id="obs-1", evidence_id=None, retain_evidence=False,
        reason_for_retention=None, transcription_policy=TranscriptionReliabilityPolicy(.75),
    ))
    assert result.status is CommandStatus.REJECTED
    assert result.reason == "domain-validation-error"
    assert repository.batches == []


def test_profile_change_is_proposed_by_domain_policy_and_not_consolidated():
    repository = Repository()
    reliability = TranscriptionReliabilityPolicy(.75)
    records = []
    for index, activity_id in enumerate(("a-1", "a-2", "a-3"), 1):
        observation = Observation.from_answer(
            observation_id=f"obs-{index}", learner_id="learner-1", session_id="session-1",
            objective_id="units-tens", activity_id=activity_id, answer_outcome="correct",
            stt_confidence=.95, assistance_level=0, transcription_policy=reliability,
        )
        records.append(EvidenceRecord.initial(evidence_id=f"ev-{index}", learner_id="learner-1", observation=observation))
    repository.evidence = tuple(records)
    repository.estimate = SkillEstimate("learner-1", "units-tens", CompetencyState.NOT_OBSERVED)
    thresholds = tuple(
        AssistanceThreshold(state, 3)
        for state in (
            CompetencyState.EXPLORING, CompetencyState.WITH_INTENSIVE_HELP,
            CompetencyState.WITH_LIGHT_HELP, CompetencyState.INDEPENDENT,
            CompetencyState.GENERALIZED,
        )
    )
    runtime = SessionRuntime(); runtime.start_generation("session-1")
    result = TutoringService(repository, runtime).propose_profile_change(base(
        ProposeProfileChange, objective_id="units-tens",
        progression_policy=ProgressionPolicy(thresholds),
    ))
    assert result.status is CommandStatus.APPLIED
    assert repository.batches[0].profile_change_proposals[0].to_state is CompetencyState.EXPLORING
    assert repository.estimate.state is CompetencyState.NOT_OBSERVED
