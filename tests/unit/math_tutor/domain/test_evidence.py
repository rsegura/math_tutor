from dataclasses import FrozenInstanceError

import pytest

from math_tutor.domain.evidence import (
    EvidenceRecord,
    EvidenceRevision,
    InvalidEvidence,
    Observation,
    ObservationOutcome,
    TranscriptionReliabilityPolicy,
)
from math_tutor.domain.mathematics import AnswerOutcome

STT_POLICY = TranscriptionReliabilityPolicy(min_confidence=0.5)


def test_low_confidence_transcription_is_not_evaluable() -> None:
    observation = Observation.from_answer(
        observation_id="obs-1",
        learner_id="learner-1",
        session_id="session-1",
        objective_id="units-tens",
        activity_id="activity-1",
        answer_outcome="incorrect",
        stt_confidence=0.49,
        assistance_level=0,
        transcription_policy=STT_POLICY,
    )

    assert observation.outcome is ObservationOutcome.NOT_EVALUABLE


def test_observation_accepts_the_deterministic_math_outcome() -> None:
    observation = Observation.from_answer(
        observation_id="obs-1",
        learner_id="learner-1",
        session_id="session-1",
        objective_id="units-tens",
        activity_id="activity-1",
        answer_outcome=AnswerOutcome.CORRECT,
        stt_confidence=0.99,
        assistance_level=0,
        transcription_policy=STT_POLICY,
    )

    assert observation.outcome is ObservationOutcome.CORRECT


def test_observed_facts_are_immutable() -> None:
    observation = observation_fixture()

    with pytest.raises(FrozenInstanceError):
        observation.assistance_level = 4  # type: ignore[misc]


def test_interpretation_correction_appends_version_without_changing_facts() -> None:
    original = EvidenceRecord.initial(
        evidence_id="evidence-1",
        learner_id="learner-1",
        observation=observation_fixture(),
        interpretation="El alumno confundió decenas y unidades.",
        reason_for_retention="repeated-error",
    )

    corrected = original.revise_interpretation(
        interpretation="La transcripción no permite atribuir un error.",
        reason="therapist-correction",
    )

    assert corrected.observation is original.observation
    assert [revision.version for revision in corrected.interpretations] == [1, 2]
    assert corrected.current_interpretation == "La transcripción no permite atribuir un error."
    assert original.current_interpretation == "El alumno confundió decenas y unidades."


def test_revision_versions_must_be_contiguous() -> None:
    with pytest.raises(InvalidEvidence, match="contiguous"):
        EvidenceRecord(
            evidence_id="evidence-1",
            learner_id="learner-1",
            observation=observation_fixture(),
            reason_for_retention="repeated-error",
            interpretations=(
                EvidenceRevision(1, "initial", "initial-proposal"),
                EvidenceRevision(3, "bad", "correction"),
            ),
        )


def observation_fixture() -> Observation:
    return Observation.from_answer(
        observation_id="obs-1",
        learner_id="learner-1",
        session_id="session-1",
        objective_id="units-tens",
        activity_id="activity-1",
        answer_outcome="incorrect",
        stt_confidence=0.99,
        assistance_level=1,
        transcription_policy=STT_POLICY,
    )
