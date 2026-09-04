from dataclasses import FrozenInstanceError

import pytest

from math_tutor.domain.learning import (
    CompetencyState,
    AssistanceThreshold,
    InvalidLearningState,
    LearningPlan,
    LearningSession,
    PresentationProfile,
    ProgressionPolicy,
    SkillEstimate,
)
from math_tutor.domain.evidence import (
    EvidenceRecord,
    Observation,
    ObservationOutcome,
    TranscriptionReliabilityPolicy,
)


STT_POLICY = TranscriptionReliabilityPolicy(min_confidence=0.75)
ASSISTANCE_THRESHOLDS = (
    AssistanceThreshold(CompetencyState.EXPLORING, 3),
    AssistanceThreshold(CompetencyState.WITH_INTENSIVE_HELP, 3),
    AssistanceThreshold(CompetencyState.WITH_LIGHT_HELP, 2),
    AssistanceThreshold(CompetencyState.INDEPENDENT, 0),
    AssistanceThreshold(CompetencyState.GENERALIZED, 0),
)


def test_plan_activates_only_therapist_authorised_objectives() -> None:
    with pytest.raises(InvalidLearningState, match="not authorised"):
        LearningPlan(
            learner_id="learner-1",
            authorised_objective_ids=("units-tens",),
            active_objective_ids=("addition",),
            presentation=PresentationProfile.for_age(8),
        )


def test_age_changes_presentation_but_not_competency() -> None:
    younger = PresentationProfile.for_age(7)
    older = PresentationProfile.for_age(11)
    estimate = SkillEstimate("learner-1", "units-tens", CompetencyState.EXPLORING)

    assert younger != older
    assert estimate.state is CompetencyState.EXPLORING


def test_one_success_cannot_advance_competency() -> None:
    policy = ProgressionPolicy(
        assistance_thresholds=ASSISTANCE_THRESHOLDS,
        min_successes=2,
        min_distinct_activities=2,
    )
    estimate = SkillEstimate("learner-1", "units-tens", CompetencyState.EXPLORING)
    evidence = (successful_evidence("e-1", "activity-1"),)

    assert policy.propose_change(estimate, evidence) is None


def test_policy_cannot_be_configured_to_advance_from_one_success() -> None:
    with pytest.raises(InvalidLearningState, match="at least 2"):
        ProgressionPolicy(
            assistance_thresholds=ASSISTANCE_THRESHOLDS, min_successes=1
        )


def test_varied_repeated_success_can_propose_exactly_one_step() -> None:
    policy = ProgressionPolicy(
        assistance_thresholds=ASSISTANCE_THRESHOLDS,
        min_successes=2,
        min_distinct_activities=2,
    )
    estimate = SkillEstimate("learner-1", "units-tens", CompetencyState.EXPLORING)
    evidence = (
        successful_evidence("e-1", "activity-1"),
        successful_evidence("e-2", "activity-2"),
    )

    proposal = policy.propose_change(estimate, evidence)

    assert proposal is not None
    assert proposal.from_state is CompetencyState.EXPLORING
    assert proposal.to_state is CompetencyState.WITH_INTENSIVE_HELP
    assert proposal.evidence_ids == ("e-1", "e-2")


def test_low_confidence_stt_cannot_lower_competency() -> None:
    policy = ProgressionPolicy(
        assistance_thresholds=ASSISTANCE_THRESHOLDS,
        min_successes=2,
        min_distinct_activities=2,
        min_stt_confidence=0.75,
        min_failures_for_review=2,
    )
    estimate = SkillEstimate("learner-1", "units-tens", CompetencyState.INDEPENDENT)
    evidence = (
        failed_evidence("e-1", "activity-1", stt_confidence=0.3),
        failed_evidence("e-2", "activity-2", stt_confidence=0.4),
    )

    assert all(item.observation.outcome is ObservationOutcome.NOT_EVALUABLE for item in evidence)
    assert policy.propose_change(estimate, evidence) is None


def test_repeated_reliable_failures_propose_review_not_silent_regression() -> None:
    policy = ProgressionPolicy(
        assistance_thresholds=ASSISTANCE_THRESHOLDS,
        min_failures_for_review=2,
    )
    estimate = SkillEstimate("learner-1", "units-tens", CompetencyState.INDEPENDENT)
    evidence = (
        failed_evidence("e-1", "activity-1", stt_confidence=0.95),
        failed_evidence("e-2", "activity-2", stt_confidence=0.95),
    )

    proposal = policy.propose_change(estimate, evidence)

    assert proposal is not None
    assert proposal.to_state is CompetencyState.NEEDS_REVIEW


def test_stop_request_terminates_session_immediately() -> None:
    session = LearningSession.start(session_id="session-1", plan=plan_fixture())

    stopped = session.request_stop(reason="learner-request")

    assert stopped.ended
    assert stopped.stop_requested
    assert stopped.end_reason == "learner-request"
    assert stopped.can_continue is False


def test_session_cannot_remain_active_after_a_stop_request() -> None:
    with pytest.raises(InvalidLearningState, match="stop request must end"):
        LearningSession(
            session_id="session-1",
            learner_id="learner-1",
            plan_id="plan-1",
            plan_version=1,
            authorised_objective_ids=("units-tens",),
            active_objective_ids=("units-tens",),
            stop_requested=True,
        )


def test_session_rejects_an_active_objective_outside_its_plan_snapshot() -> None:
    with pytest.raises(InvalidLearningState, match="not authorised"):
        LearningSession(
            session_id="session-1",
            learner_id="learner-1",
            plan_id="plan-1",
            plan_version=1,
            authorised_objective_ids=("units-tens",),
            active_objective_ids=("addition",),
        )


def test_learning_state_is_immutable() -> None:
    estimate = SkillEstimate("learner-1", "units-tens", CompetencyState.EXPLORING)

    with pytest.raises(FrozenInstanceError):
        estimate.state = CompetencyState.INDEPENDENT  # type: ignore[misc]


def successful_evidence(evidence_id: str, activity_id: str) -> EvidenceRecord:
    observation = Observation.from_answer(
        observation_id=f"o-{evidence_id}",
        learner_id="learner-1",
        session_id="session-1",
        objective_id="units-tens",
        activity_id=activity_id,
        answer_outcome="correct",
        stt_confidence=0.95,
        assistance_level=0,
        transcription_policy=STT_POLICY,
    )
    return EvidenceRecord.initial(
        evidence_id=evidence_id,
        learner_id="learner-1",
        observation=observation,
    )


def failed_evidence(
    evidence_id: str, activity_id: str, *, stt_confidence: float
) -> EvidenceRecord:
    observation = Observation.from_answer(
        observation_id=f"o-{evidence_id}",
        learner_id="learner-1",
        session_id="session-1",
        objective_id="units-tens",
        activity_id=activity_id,
        answer_outcome="incorrect",
        stt_confidence=stt_confidence,
        assistance_level=0,
        transcription_policy=STT_POLICY,
    )
    return EvidenceRecord.initial(
        evidence_id=evidence_id,
        learner_id="learner-1",
        observation=observation,
    )


def plan_fixture() -> LearningPlan:
    return LearningPlan(
        learner_id="learner-1",
        authorised_objective_ids=("units-tens",),
        active_objective_ids=("units-tens",),
        presentation=PresentationProfile.for_age(8),
        plan_id="plan-1",
    )
