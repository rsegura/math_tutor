"""Immutable observations and versioned interpretations of learning evidence."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


class InvalidEvidence(ValueError):
    """Raised when evidence cannot preserve its audit invariants."""


class ObservationOutcome(Enum):
    """Deterministic evaluation attached to an observed learner turn."""

    CORRECT = "correct"
    INCORRECT = "incorrect"
    AMBIGUOUS = "ambiguous"
    NOT_EVALUABLE = "not-evaluable"


def _require_id(value: object, label: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise InvalidEvidence(f"{label} must be a trimmed, nonempty string")


@dataclass(frozen=True, slots=True)
class TranscriptionReliabilityPolicy:
    """Required boundary policy for attributing a transcription to a learner."""

    min_confidence: float

    def __post_init__(self) -> None:
        if (
            isinstance(self.min_confidence, bool)
            or not isinstance(self.min_confidence, (int, float))
            or not 0 <= self.min_confidence <= 1
        ):
            raise InvalidEvidence(
                "minimum reliable STT confidence must be between 0 and 1"
            )


@dataclass(frozen=True, slots=True)
class Observation:
    """Observed facts from one activity turn; never revised in place."""

    observation_id: str
    learner_id: str
    session_id: str
    objective_id: str
    activity_id: str
    outcome: ObservationOutcome
    stt_confidence: float
    assistance_level: int
    transcription_policy: TranscriptionReliabilityPolicy
    response_text: str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.observation_id, "observation id"),
            (self.learner_id, "learner id"),
            (self.session_id, "session id"),
            (self.objective_id, "objective id"),
            (self.activity_id, "activity id"),
        ):
            _require_id(value, label)
        if not isinstance(self.outcome, ObservationOutcome):
            raise InvalidEvidence("outcome must be an ObservationOutcome")
        if (
            isinstance(self.stt_confidence, bool)
            or not isinstance(self.stt_confidence, (int, float))
            or not 0 <= self.stt_confidence <= 1
        ):
            raise InvalidEvidence("STT confidence must be between 0 and 1")
        if not isinstance(self.transcription_policy, TranscriptionReliabilityPolicy):
            raise TypeError("transcription_policy must be a TranscriptionReliabilityPolicy")
        if self.stt_confidence < self.transcription_policy.min_confidence:
            object.__setattr__(self, "outcome", ObservationOutcome.NOT_EVALUABLE)
        if (
            isinstance(self.assistance_level, bool)
            or not isinstance(self.assistance_level, int)
            or self.assistance_level < 0
        ):
            raise InvalidEvidence("assistance level must be a nonnegative integer")
        if self.response_text is not None and not isinstance(self.response_text, str):
            raise InvalidEvidence("response text must be text or None")

    @classmethod
    def from_answer(
        cls,
        *,
        observation_id: str,
        learner_id: str,
        session_id: str,
        objective_id: str,
        activity_id: str,
        answer_outcome: str | Enum,
        stt_confidence: float,
        assistance_level: int,
        response_text: str | None = None,
        transcription_policy: TranscriptionReliabilityPolicy,
    ) -> Observation:
        """Build facts while refusing to attribute unreliable STT as failure."""

        if not isinstance(transcription_policy, TranscriptionReliabilityPolicy):
            raise TypeError("transcription_policy must be a TranscriptionReliabilityPolicy")
        try:
            raw_outcome = (
                answer_outcome.value
                if isinstance(answer_outcome, Enum)
                else answer_outcome
            )
            outcome = ObservationOutcome(raw_outcome)
        except (TypeError, ValueError) as error:
            raise InvalidEvidence("unknown answer outcome") from error
        if stt_confidence < transcription_policy.min_confidence:
            outcome = ObservationOutcome.NOT_EVALUABLE
        return cls(
            observation_id=observation_id,
            learner_id=learner_id,
            session_id=session_id,
            objective_id=objective_id,
            activity_id=activity_id,
            outcome=outcome,
            stt_confidence=stt_confidence,
            assistance_level=assistance_level,
            transcription_policy=transcription_policy,
            response_text=response_text,
        )


@dataclass(frozen=True, slots=True)
class EvidenceRevision:
    """One append-only interpretation of immutable observed facts."""

    version: int
    interpretation: str
    reason: str

    def __post_init__(self) -> None:
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise InvalidEvidence("revision version must be a positive integer")
        _require_id(self.interpretation, "interpretation")
        _require_id(self.reason, "revision reason")


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """Retained observation plus its independent interpretation history."""

    evidence_id: str
    learner_id: str
    observation: Observation
    reason_for_retention: str
    interpretations: tuple[EvidenceRevision, ...] = ()

    def __post_init__(self) -> None:
        _require_id(self.evidence_id, "evidence id")
        _require_id(self.learner_id, "learner id")
        if not isinstance(self.observation, Observation):
            raise InvalidEvidence("evidence must reference an Observation")
        if self.observation.learner_id != self.learner_id:
            raise InvalidEvidence("evidence learner must match observation learner")
        _require_id(self.reason_for_retention, "retention reason")
        revisions = tuple(self.interpretations)
        if not all(isinstance(item, EvidenceRevision) for item in revisions):
            raise InvalidEvidence("interpretations must be EvidenceRevision values")
        if tuple(item.version for item in revisions) != tuple(range(1, len(revisions) + 1)):
            raise InvalidEvidence("interpretation versions must be contiguous from 1")
        object.__setattr__(self, "interpretations", revisions)

    @classmethod
    def initial(
        cls,
        *,
        evidence_id: str,
        learner_id: str,
        observation: Observation,
        interpretation: str | None = None,
        reason_for_retention: str = "progression-evidence",
    ) -> EvidenceRecord:
        revisions = ()
        if interpretation is not None:
            revisions = (EvidenceRevision(1, interpretation, "initial-proposal"),)
        return cls(evidence_id, learner_id, observation, reason_for_retention, revisions)

    @property
    def current_interpretation(self) -> str | None:
        return self.interpretations[-1].interpretation if self.interpretations else None

    def revise_interpretation(self, *, interpretation: str, reason: str) -> EvidenceRecord:
        revision = EvidenceRevision(len(self.interpretations) + 1, interpretation, reason)
        return replace(self, interpretations=(*self.interpretations, revision))
