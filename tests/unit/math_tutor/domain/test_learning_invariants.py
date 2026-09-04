import pytest

from math_tutor.domain.evidence import (
    EvidenceRecord,
    InvalidEvidence,
    Observation,
    ObservationOutcome,
    TranscriptionReliabilityPolicy,
)
from math_tutor.domain.learning import (
    AssistanceThreshold,
    CompetencyState,
    InvalidLearningState,
    LearningPlan,
    LearningSession,
    PresentationProfile,
    ProgressionPolicy,
    ProposedProfileChange,
    SkillEstimate,
)


STT = TranscriptionReliabilityPolicy(min_confidence=0.75)


def test_observation_requires_a_typed_transcription_policy() -> None:
    with pytest.raises(TypeError):
        observation("e-1", "a-1", transcription_policy=None)  # type: ignore[arg-type]


def test_low_confidence_is_always_not_evaluable() -> None:
    item = observation("e-1", "a-1", confidence=0.74)
    assert item.outcome is ObservationOutcome.NOT_EVALUABLE


def test_direct_observation_construction_cannot_bypass_transcription_policy() -> None:
    item = Observation(
        observation_id="o-1",
        learner_id="learner-1",
        session_id="session-1",
        objective_id="units-tens",
        activity_id="a-1",
        outcome=ObservationOutcome.INCORRECT,
        stt_confidence=0.74,
        assistance_level=0,
        transcription_policy=STT,
    )
    assert item.outcome is ObservationOutcome.NOT_EVALUABLE


@pytest.mark.parametrize("confidence", [-0.1, 1.1, True])
def test_transcription_policy_rejects_invalid_thresholds(confidence: object) -> None:
    with pytest.raises(InvalidEvidence):
        TranscriptionReliabilityPolicy(min_confidence=confidence)  # type: ignore[arg-type]


def test_proposal_rejects_invalid_types_versions_ids_and_transitions() -> None:
    base = dict(
        learner_id="learner-1",
        objective_id="units-tens",
        from_state=CompetencyState.EXPLORING,
        to_state=CompetencyState.WITH_INTENSIVE_HELP,
        evidence_ids=("e-1",),
        estimate_version=1,
        policy_version="p1",
    )
    for override in (
        {"from_state": "exploring"},
        {"to_state": "with-intensive-help"},
        {"estimate_version": 0},
        {"evidence_ids": ("",)},
        {"evidence_ids": ("e-1", "e-1")},
        {"to_state": CompetencyState.INDEPENDENT},
    ):
        with pytest.raises(InvalidLearningState):
            ProposedProfileChange(**(base | override))  # type: ignore[arg-type]


def test_review_is_an_explicit_allowed_downgrade() -> None:
    proposal = ProposedProfileChange(
        learner_id="learner-1",
        objective_id="units-tens",
        from_state=CompetencyState.INDEPENDENT,
        to_state=CompetencyState.NEEDS_REVIEW,
        evidence_ids=("e-1",),
        estimate_version=1,
        policy_version="p1",
    )
    assert proposal.to_state is CompetencyState.NEEDS_REVIEW


def test_failures_before_any_observation_do_not_create_an_invalid_review_transition() -> None:
    estimate = estimate_fixture(state=CompetencyState.NOT_OBSERVED)
    failures = (
        evidence("e-1", "a-1", outcome="incorrect"),
        evidence("e-2", "a-2", outcome="incorrect"),
    )
    assert policy().propose_change(estimate, failures) is None


def test_consumed_evidence_cannot_be_replayed() -> None:
    estimate = estimate_fixture(supporting_evidence_ids=("e-1",))
    assert policy().propose_change(
        estimate, (evidence("e-1", "a-1"), evidence("e-2", "a-2"))
    ) is None


def test_identical_duplicate_evidence_is_counted_once() -> None:
    first = evidence("e-1", "a-1")
    assert policy().propose_change(estimate_fixture(), (first, first, evidence("e-2", "a-2"))) is not None


def test_conflicting_duplicate_evidence_or_observation_fails_closed() -> None:
    first = evidence("e-1", "a-1", observation_id="o-shared")
    for duplicate in (
        evidence("e-1", "a-2", observation_id="o-other"),
        evidence("e-2", "a-2", observation_id="o-shared"),
    ):
        with pytest.raises(InvalidLearningState, match="conflicting duplicate"):
            policy().propose_change(estimate_fixture(), (first, duplicate))


def test_high_assistance_does_not_prove_independence() -> None:
    estimate = estimate_fixture(state=CompetencyState.WITH_LIGHT_HELP)
    items = (evidence("e-1", "a-1", assistance=1), evidence("e-2", "a-2", assistance=1))
    assert policy().propose_change(estimate, items) is None


def test_generalization_requires_distinct_sessions() -> None:
    estimate = estimate_fixture(state=CompetencyState.INDEPENDENT)
    same_session = (evidence("e-1", "a-1"), evidence("e-2", "a-2"))
    assert policy().propose_change(estimate, same_session) is None
    cross_session = (evidence("e-1", "a-1"), evidence("e-2", "a-2", session_id="session-2"))
    assert policy().propose_change(estimate, cross_session) is not None


def test_cross_learner_evidence_fails_closed() -> None:
    with pytest.raises(InvalidLearningState, match="learner"):
        policy().propose_change(
            estimate_fixture(),
            (evidence("e-1", "a-1"), evidence("e-2", "a-2", learner_id="learner-2")),
        )


def test_session_snapshots_learner_and_plan_version() -> None:
    plan = LearningPlan(
        learner_id="learner-1",
        authorised_objective_ids=("units-tens",),
        active_objective_ids=("units-tens",),
        presentation=PresentationProfile.for_age(8),
        version=4,
    )
    session = LearningSession.start(session_id="session-1", plan=plan)
    assert (session.learner_id, session.plan_version) == ("learner-1", 4)


def test_evidence_rejects_cross_learner_observation() -> None:
    with pytest.raises(InvalidEvidence, match="learner"):
        EvidenceRecord.initial(
            evidence_id="e-1",
            learner_id="learner-2",
            observation=observation("e-1", "a-1"),
        )


def test_progression_requires_complete_unique_assistance_thresholds() -> None:
    with pytest.raises(InvalidLearningState, match="every progression target"):
        ProgressionPolicy(assistance_thresholds=(AssistanceThreshold(CompetencyState.EXPLORING, 1),))


def test_generalization_policy_cannot_require_fewer_than_two_sessions() -> None:
    with pytest.raises(InvalidLearningState, match="at least 2"):
        ProgressionPolicy(
            assistance_thresholds=policy().assistance_thresholds,
            min_sessions_for_generalization=1,
        )


def policy() -> ProgressionPolicy:
    return ProgressionPolicy(
        min_successes=2,
        min_distinct_activities=2,
        min_stt_confidence=0.75,
        min_failures_for_review=2,
        min_sessions_for_generalization=2,
        assistance_thresholds=(
            AssistanceThreshold(CompetencyState.EXPLORING, 3),
            AssistanceThreshold(CompetencyState.WITH_INTENSIVE_HELP, 3),
            AssistanceThreshold(CompetencyState.WITH_LIGHT_HELP, 2),
            AssistanceThreshold(CompetencyState.INDEPENDENT, 0),
            AssistanceThreshold(CompetencyState.GENERALIZED, 0),
        ),
    )


def estimate_fixture(*, state: CompetencyState = CompetencyState.EXPLORING, supporting_evidence_ids: tuple[str, ...] = ()) -> SkillEstimate:
    return SkillEstimate("learner-1", "units-tens", state, supporting_evidence_ids=supporting_evidence_ids)


def evidence(evidence_id: str, activity_id: str, **kwargs: object) -> EvidenceRecord:
    item = observation(evidence_id, activity_id, **kwargs)
    return EvidenceRecord.initial(evidence_id=evidence_id, learner_id=item.learner_id, observation=item)


def observation(
    evidence_id: str,
    activity_id: str,
    *,
    learner_id: str = "learner-1",
    session_id: str = "session-1",
    observation_id: str | None = None,
    confidence: float = 0.95,
    assistance: int = 0,
    outcome: str = "correct",
    transcription_policy: TranscriptionReliabilityPolicy = STT,
) -> Observation:
    return Observation.from_answer(
        observation_id=observation_id or f"o-{evidence_id}",
        learner_id=learner_id,
        session_id=session_id,
        objective_id="units-tens",
        activity_id=activity_id,
        answer_outcome=outcome,
        stt_confidence=confidence,
        assistance_level=assistance,
        transcription_policy=transcription_policy,
    )
