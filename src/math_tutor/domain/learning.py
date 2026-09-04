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
    learner_id: str
    plan_id: str
    plan_version: int
    authorised_objective_ids: tuple[str, ...]
    active_objective_ids: tuple[str, ...]
    ended: bool = False
    stop_requested: bool = False
    end_reason: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        _require_id(self.session_id, "session id")
        _require_id(self.learner_id, "learner id")
        _require_id(self.plan_id, "plan id")
        if (
            isinstance(self.plan_version, bool)
            or not isinstance(self.plan_version, int)
            or self.plan_version < 1
        ):
            raise InvalidLearningState("plan version must be a positive integer")
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
            learner_id=plan.learner_id,
            plan_id=plan.plan_id,
            plan_version=plan.version,
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

    learner_id: str
    objective_id: str
    state: CompetencyState
    version: int = 1
    supporting_evidence_ids: tuple[str, ...] = ()
    supporting_observation_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_id(self.learner_id, "learner id")
        _require_id(self.objective_id, "objective id")
        if not isinstance(self.state, CompetencyState):
            raise InvalidLearningState("state must be a CompetencyState")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise InvalidLearningState("estimate version must be a positive integer")
        evidence_ids = _ids(self.supporting_evidence_ids, "evidence id")
        observation_ids = _ids(
            self.supporting_observation_ids, "observation id"
        )
        if len(evidence_ids) != len(observation_ids):
            raise InvalidLearningState(
                "supporting evidence and observation ids must be aligned"
            )
        object.__setattr__(self, "supporting_evidence_ids", evidence_ids)
        object.__setattr__(self, "supporting_observation_ids", observation_ids)

    def apply(self, proposal: ProposedProfileChange) -> SkillEstimate:
        """Consolidate an exact proposal while retaining consumed identities."""

        if not isinstance(proposal, ProposedProfileChange):
            raise InvalidLearningState("estimate update requires a ProposedProfileChange")
        if proposal.learner_id != self.learner_id:
            raise InvalidLearningState("proposal learner must match estimate learner")
        if proposal.objective_id != self.objective_id:
            raise InvalidLearningState("proposal objective must match estimate objective")
        if proposal.from_state is not self.state:
            raise InvalidLearningState("proposal source state must match estimate state")
        if proposal.estimate_version != self.version:
            raise InvalidLearningState("proposal version must match estimate version")
        if set(proposal.evidence_ids) & set(self.supporting_evidence_ids):
            raise InvalidLearningState("proposal reuses consumed evidence")
        if set(proposal.observation_ids) & set(self.supporting_observation_ids):
            raise InvalidLearningState("proposal reuses consumed observation")
        return replace(
            self,
            state=proposal.to_state,
            version=self.version + 1,
            supporting_evidence_ids=(
                *self.supporting_evidence_ids,
                *proposal.evidence_ids,
            ),
            supporting_observation_ids=(
                *self.supporting_observation_ids,
                *proposal.observation_ids,
            ),
        )


@dataclass(frozen=True, slots=True)
class ProposedProfileChange:
    """Auditable proposal; it does not mutate or consolidate learner state."""

    learner_id: str
    objective_id: str
    from_state: CompetencyState
    to_state: CompetencyState
    evidence_ids: tuple[str, ...]
    observation_ids: tuple[str, ...]
    estimate_version: int
    policy_version: str

    def __post_init__(self) -> None:
        _require_id(self.learner_id, "learner id")
        _require_id(self.objective_id, "objective id")
        _require_id(self.policy_version, "policy version")
        if not isinstance(self.from_state, CompetencyState) or not isinstance(
            self.to_state, CompetencyState
        ):
            raise InvalidLearningState("proposal states must be CompetencyState values")
        if (
            isinstance(self.estimate_version, bool)
            or not isinstance(self.estimate_version, int)
            or self.estimate_version < 1
        ):
            raise InvalidLearningState("estimate version must be a positive integer")
        evidence_ids = _ids(self.evidence_ids, "evidence id")
        observation_ids = _ids(self.observation_ids, "observation id")
        if not evidence_ids:
            raise InvalidLearningState("proposal requires evidence")
        if len(evidence_ids) != len(observation_ids):
            raise InvalidLearningState(
                "proposal evidence and observation ids must be aligned"
            )
        normal_step = (
            self.from_state in _PROGRESSION[:-1]
            and self.to_state is _PROGRESSION[_PROGRESSION.index(self.from_state) + 1]
        )
        explicit_review = (
            self.from_state in _PROGRESSION[1:]
            and self.to_state is CompetencyState.NEEDS_REVIEW
        )
        if not (normal_step or explicit_review):
            raise InvalidLearningState("proposal must represent exactly one allowed transition")
        object.__setattr__(self, "evidence_ids", evidence_ids)
        object.__setattr__(self, "observation_ids", observation_ids)


@dataclass(frozen=True, slots=True)
class AssistanceThreshold:
    """Maximum assistance that can support progression to one target state."""

    to_state: CompetencyState
    max_assistance_level: int

    def __post_init__(self) -> None:
        if self.to_state not in _PROGRESSION[1:]:
            raise InvalidLearningState("assistance threshold requires a progression target")
        if (
            isinstance(self.max_assistance_level, bool)
            or not isinstance(self.max_assistance_level, int)
            or self.max_assistance_level < 0
        ):
            raise InvalidLearningState(
                "maximum assistance level must be a nonnegative integer"
            )


@dataclass(frozen=True, slots=True)
class ProgressionPolicy:
    """Injected, versioned thresholds for provisional one-step progression."""

    assistance_thresholds: tuple[AssistanceThreshold, ...]
    min_successes: int = 3
    min_distinct_activities: int = 2
    min_stt_confidence: float = 0.75
    min_failures_for_review: int = 3
    min_sessions_for_generalization: int = 2
    version: str = "progression-v1"

    def __post_init__(self) -> None:
        integer_thresholds = (
            (self.min_successes, "minimum successes"),
            (self.min_distinct_activities, "minimum distinct activities"),
            (self.min_failures_for_review, "minimum failures for review"),
            (
                self.min_sessions_for_generalization,
                "minimum sessions for generalization",
            ),
        )
        for value, label in integer_thresholds:
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise InvalidLearningState(f"{label} must be a positive integer")
        if self.min_successes < 2:
            raise InvalidLearningState("minimum successes must be at least 2")
        if self.min_distinct_activities < 2:
            raise InvalidLearningState(
                "minimum distinct activities must be at least 2"
            )
        if self.min_sessions_for_generalization < 2:
            raise InvalidLearningState(
                "minimum sessions for generalization must be at least 2"
            )
        if (
            isinstance(self.min_stt_confidence, bool)
            or not isinstance(self.min_stt_confidence, (int, float))
            or not 0 <= self.min_stt_confidence <= 1
        ):
            raise InvalidLearningState("minimum STT confidence must be between 0 and 1")
        _require_id(self.version, "policy version")
        thresholds = tuple(self.assistance_thresholds)
        if not all(isinstance(item, AssistanceThreshold) for item in thresholds):
            raise InvalidLearningState(
                "assistance thresholds must be AssistanceThreshold values"
            )
        states = tuple(item.to_state for item in thresholds)
        expected = _PROGRESSION[1:]
        if len(states) != len(set(states)) or set(states) != set(expected):
            raise InvalidLearningState(
                "assistance thresholds must define every progression target exactly once"
            )
        object.__setattr__(self, "assistance_thresholds", thresholds)

    def propose_change(
        self, estimate: SkillEstimate, evidence: tuple[EvidenceRecord, ...]
    ) -> ProposedProfileChange | None:
        """Propose, but never apply, one supported transition."""

        if not isinstance(estimate, SkillEstimate):
            raise InvalidLearningState("progression requires a SkillEstimate")
        records = self._deduplicated_evidence(evidence)
        if any(record.learner_id != estimate.learner_id for record in records):
            raise InvalidLearningState("evidence learner must match estimate learner")
        consumed = set(estimate.supporting_evidence_ids)
        consumed_observations = set(estimate.supporting_observation_ids)

        relevant = tuple(
            record
            for record in records
            if record.observation.objective_id == estimate.objective_id
            and record.evidence_id not in consumed
            and record.observation.observation_id not in consumed_observations
            and record.observation.stt_confidence >= self.min_stt_confidence
            and record.observation.outcome is not ObservationOutcome.NOT_EVALUABLE
        )
        failures = tuple(
            record
            for record in relevant
            if record.observation.outcome is ObservationOutcome.INCORRECT
        )
        if len(failures) >= self.min_failures_for_review:
            if estimate.state in (
                CompetencyState.NOT_OBSERVED,
                CompetencyState.NEEDS_REVIEW,
            ):
                return None
            return ProposedProfileChange(
                learner_id=estimate.learner_id,
                objective_id=estimate.objective_id,
                from_state=estimate.state,
                to_state=CompetencyState.NEEDS_REVIEW,
                evidence_ids=tuple(record.evidence_id for record in failures),
                observation_ids=tuple(
                    record.observation.observation_id for record in failures
                ),
                estimate_version=estimate.version,
                policy_version=self.version,
            )
        successes = tuple(record for record in relevant if record.observation.outcome is ObservationOutcome.CORRECT)
        distinct_activities = {record.observation.activity_id for record in successes}
        if len(successes) < self.min_successes or len(distinct_activities) < self.min_distinct_activities:
            return None
        if estimate.state in (
            CompetencyState.NEEDS_REVIEW,
            CompetencyState.GENERALIZED,
        ):
            return None
        next_state = _PROGRESSION[_PROGRESSION.index(estimate.state) + 1]
        max_assistance = next(
            item.max_assistance_level
            for item in self.assistance_thresholds
            if item.to_state is next_state
        )
        successes = tuple(
            record
            for record in successes
            if record.observation.assistance_level <= max_assistance
        )
        distinct_activities = {
            record.observation.activity_id for record in successes
        }
        if (
            len(successes) < self.min_successes
            or len(distinct_activities) < self.min_distinct_activities
        ):
            return None
        if next_state is CompetencyState.GENERALIZED:
            sessions = {record.observation.session_id for record in successes}
            if len(sessions) < self.min_sessions_for_generalization:
                return None
        return ProposedProfileChange(
            learner_id=estimate.learner_id,
            objective_id=estimate.objective_id,
            from_state=estimate.state,
            to_state=next_state,
            evidence_ids=tuple(record.evidence_id for record in successes),
            observation_ids=tuple(
                record.observation.observation_id for record in successes
            ),
            estimate_version=estimate.version,
            policy_version=self.version,
        )

    @staticmethod
    def _deduplicated_evidence(
        evidence: tuple[EvidenceRecord, ...],
    ) -> tuple[EvidenceRecord, ...]:
        records = tuple(evidence)
        if not all(isinstance(record, EvidenceRecord) for record in records):
            raise InvalidLearningState("evidence must contain EvidenceRecord values")
        by_evidence_id: dict[str, EvidenceRecord] = {}
        by_observation_id: dict[str, EvidenceRecord] = {}
        result: list[EvidenceRecord] = []
        for record in records:
            prior_evidence = by_evidence_id.get(record.evidence_id)
            prior_observation = by_observation_id.get(record.observation.observation_id)
            if prior_evidence is not None:
                if prior_evidence != record:
                    raise InvalidLearningState("conflicting duplicate evidence id")
                continue
            if prior_observation is not None:
                if prior_observation.observation != record.observation:
                    raise InvalidLearningState("conflicting duplicate observation id")
                continue
            by_evidence_id[record.evidence_id] = record
            by_observation_id[record.observation.observation_id] = record
            result.append(record)
        return tuple(result)
