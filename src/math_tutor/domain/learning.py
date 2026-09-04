"""Pure state and deterministic policy for bounded learning progression."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from math_tutor.domain.evidence import EvidenceRecord, ObservationOutcome


class InvalidLearningState(ValueError):
    """Raised when learning state violates an educational boundary."""


class CompetencyState(Enum):
    """Observable competency states, independent from learner age."""

    NOT_OBSERVED = "not-observed"
    EXPLORING = "exploring"
    WITH_INTENSIVE_HELP = "with-intensive-help"
    WITH_LIGHT_HELP = "with-light-help"
    INDEPENDENT = "independent"
    GENERALIZED = "generalized"
    NEEDS_REVIEW = "needs-review"


_PROGRESSION = (
    CompetencyState.NOT_OBSERVED,
    CompetencyState.EXPLORING,
    CompetencyState.WITH_INTENSIVE_HELP,
    CompetencyState.WITH_LIGHT_HELP,
    CompetencyState.INDEPENDENT,
    CompetencyState.GENERALIZED,
)


def _require_id(value: object, label: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise InvalidLearningState(f"{label} must be a trimmed, nonempty string")


def _ids(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    result = tuple(values)
    for value in result:
        _require_id(value, label)
    if len(result) != len(set(result)):
        raise InvalidLearningState(f"duplicate {label}")
    return result


@dataclass(frozen=True, slots=True)
class PresentationProfile:
    """Age-sensitive interaction metadata with no mathematical estimate."""

    learner_age: int
    language_style: str
    instruction_length: str

    def __post_init__(self) -> None:
        if isinstance(self.learner_age, bool) or not isinstance(self.learner_age, int) or not 6 <= self.learner_age <= 13:
            raise InvalidLearningState("learner age must be between 6 and 13")
        _require_id(self.language_style, "language style")
        _require_id(self.instruction_length, "instruction length")

    @classmethod
    def for_age(cls, learner_age: int) -> PresentationProfile:
        if learner_age <= 8:
            return cls(learner_age, "concrete-and-playful", "short")
        if learner_age <= 10:
            return cls(learner_age, "clear-and-encouraging", "medium")
        return cls(learner_age, "age-respectful", "medium")


@dataclass(frozen=True, slots=True)
class LearningPlan:
    """Therapist-authorised scope for a learner."""

    learner_id: str
    authorised_objective_ids: tuple[str, ...]
    active_objective_ids: tuple[str, ...]
    presentation: PresentationProfile
    plan_id: str = "current-plan"
    version: int = 1

    def __post_init__(self) -> None:
        _require_id(self.learner_id, "learner id")
        _require_id(self.plan_id, "plan id")
        authorised = _ids(self.authorised_objective_ids, "authorised objective id")
        active = _ids(self.active_objective_ids, "active objective id")
        unknown = set(active) - set(authorised)
        if unknown:
            raise InvalidLearningState(f"active objective '{sorted(unknown)[0]}' is not authorised")
        if not isinstance(self.presentation, PresentationProfile):
            raise InvalidLearningState("presentation must be a PresentationProfile")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise InvalidLearningState("plan version must be a positive integer")
        object.__setattr__(self, "authorised_objective_ids", authorised)
        object.__setattr__(self, "active_objective_ids", active)

    def activate(self, objective_id: str) -> LearningPlan:
        if objective_id not in self.authorised_objective_ids:
            raise InvalidLearningState(f"objective '{objective_id}' is not authorised")
        if objective_id in self.active_objective_ids:
            return self
        return replace(self, active_objective_ids=(*self.active_objective_ids, objective_id), version=self.version + 1)


@dataclass(frozen=True, slots=True)
class LearningSession:
    """Immutable session lifecycle whose stop transition has priority."""

    session_id: str
    plan_id: str
    authorised_objective_ids: tuple[str, ...]
    active_objective_ids: tuple[str, ...]
    ended: bool = False
    stop_requested: bool = False
    end_reason: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        _require_id(self.session_id, "session id")
        _require_id(self.plan_id, "plan id")
        authorised = _ids(
            self.authorised_objective_ids, "authorised objective id"
        )
        active = _ids(self.active_objective_ids, "active objective id")
        unknown = set(active) - set(authorised)
        if unknown:
            raise InvalidLearningState(
                f"active objective '{sorted(unknown)[0]}' is not authorised"
            )
        object.__setattr__(self, "authorised_objective_ids", authorised)
        object.__setattr__(self, "active_objective_ids", active)
        if self.stop_requested and not self.ended:
            raise InvalidLearningState("a stop request must end the session")
        if self.ended and self.end_reason is None:
            raise InvalidLearningState("an ended session requires an end reason")
        if not self.ended and self.end_reason is not None:
            raise InvalidLearningState("an active session cannot have an end reason")
        if self.end_reason is not None:
            _require_id(self.end_reason, "end reason")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise InvalidLearningState("session version must be a positive integer")

    @classmethod
    def start(
        cls, *, session_id: str, plan: LearningPlan
    ) -> LearningSession:
        if not isinstance(plan, LearningPlan):
            raise InvalidLearningState("session start requires a LearningPlan")
        return cls(
            session_id=session_id,
            plan_id=plan.plan_id,
            authorised_objective_ids=plan.authorised_objective_ids,
            active_objective_ids=plan.active_objective_ids,
        )

    @property
    def can_continue(self) -> bool:
        return not self.ended and not self.stop_requested

    def request_stop(self, *, reason: str = "stop-requested") -> LearningSession:
        _require_id(reason, "stop reason")
        if self.ended:
            return self
        return replace(self, ended=True, stop_requested=True, end_reason=reason, version=self.version + 1)

    def end(self, *, reason: str) -> LearningSession:
        _require_id(reason, "end reason")
        if self.ended:
            return self
        return replace(self, ended=True, end_reason=reason, version=self.version + 1)


@dataclass(frozen=True, slots=True)
class SkillEstimate:
    """Current versioned estimate for one objective."""

    objective_id: str
    state: CompetencyState
    version: int = 1
    supporting_evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_id(self.objective_id, "objective id")
        if not isinstance(self.state, CompetencyState):
            raise InvalidLearningState("state must be a CompetencyState")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise InvalidLearningState("estimate version must be a positive integer")
        object.__setattr__(self, "supporting_evidence_ids", _ids(self.supporting_evidence_ids, "evidence id"))


@dataclass(frozen=True, slots=True)
class ProposedProfileChange:
    """Auditable proposal; it does not mutate or consolidate learner state."""

    objective_id: str
    from_state: CompetencyState
    to_state: CompetencyState
    evidence_ids: tuple[str, ...]
    estimate_version: int
    policy_version: str

    def __post_init__(self) -> None:
        _require_id(self.objective_id, "objective id")
        _require_id(self.policy_version, "policy version")
        object.__setattr__(self, "evidence_ids", _ids(self.evidence_ids, "evidence id"))


@dataclass(frozen=True, slots=True)
class ProgressionPolicy:
    """Injected, versioned thresholds for provisional one-step progression."""

    min_successes: int = 3
    min_distinct_activities: int = 2
    min_stt_confidence: float = 0.75
    min_failures_for_review: int = 3
    version: str = "progression-v1"

    def __post_init__(self) -> None:
        for value, label in ((self.min_successes, "minimum successes"), (self.min_distinct_activities, "minimum distinct activities"), (self.min_failures_for_review, "minimum failures for review")):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise InvalidLearningState(f"{label} must be a positive integer")
        if self.min_successes < 2:
            raise InvalidLearningState("minimum successes must be at least 2")
        if self.min_distinct_activities < 2:
            raise InvalidLearningState(
                "minimum distinct activities must be at least 2"
            )
        if isinstance(self.min_stt_confidence, bool) or not isinstance(self.min_stt_confidence, (int, float)) or not 0 <= self.min_stt_confidence <= 1:
            raise InvalidLearningState("minimum STT confidence must be between 0 and 1")
        _require_id(self.version, "policy version")

    def propose_change(self, estimate: SkillEstimate, evidence: tuple[EvidenceRecord, ...]) -> ProposedProfileChange | None:
        """Propose, but never apply, one supported transition."""

        relevant = tuple(
            record
            for record in evidence
            if record.observation.objective_id == estimate.objective_id
            and record.observation.stt_confidence >= self.min_stt_confidence
            and record.observation.outcome is not ObservationOutcome.NOT_EVALUABLE
        )
        failures = tuple(
            record
            for record in relevant
            if record.observation.outcome is ObservationOutcome.INCORRECT
        )
        if len(failures) >= self.min_failures_for_review:
            if estimate.state is CompetencyState.NEEDS_REVIEW:
                return None
            return ProposedProfileChange(
                objective_id=estimate.objective_id,
                from_state=estimate.state,
                to_state=CompetencyState.NEEDS_REVIEW,
                evidence_ids=tuple(record.evidence_id for record in failures),
                estimate_version=estimate.version,
                policy_version=self.version,
            )
        successes = tuple(record for record in relevant if record.observation.outcome is ObservationOutcome.CORRECT)
        distinct_activities = {record.observation.activity_id for record in successes}
        if len(successes) < self.min_successes or len(distinct_activities) < self.min_distinct_activities:
            return None
        if estimate.state is CompetencyState.NEEDS_REVIEW or estimate.state is CompetencyState.GENERALIZED:
            return None
        next_state = _PROGRESSION[_PROGRESSION.index(estimate.state) + 1]
        return ProposedProfileChange(
            objective_id=estimate.objective_id,
            from_state=estimate.state,
            to_state=next_state,
            evidence_ids=tuple(record.evidence_id for record in successes),
            estimate_version=estimate.version,
            policy_version=self.version,
        )
