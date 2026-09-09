"""Inward-facing persistence contracts for the tutoring application."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from datetime import datetime
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from math_tutor.application.provisioning import LearnerProfile, ProvisionedPlan
    from math_tutor.domain.audio_consent import AudioConsent

from math_tutor.application.results import CommandResult
from math_tutor.domain.activities import Activity
from math_tutor.domain.evidence import EvidenceRecord, Observation
from math_tutor.domain.learning import (
    LearningSession,
    ProposedProfileChange,
    SkillEstimate,
)
from math_tutor.domain.regulation import (
    ConfidenceBand, ConversationalSignal, ExecutedRegulationAction,
)


class RegulationOutcome(Enum):
    ANSWERED = "answered"
    REPEATED_DIFFICULTY = "repeated_difficulty"
    STOPPED = "stopped"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PendingRegulationEvent:
    event_id: str
    turn_id: str
    activity_id: str
    signal: ConversationalSignal
    confidence_band: ConfidenceBand
    strategy: ExecutedRegulationAction
    ordinal: int
    materialized: bool = False

    def __post_init__(self) -> None:
        for name in ("event_id", "turn_id", "activity_id"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be nonempty")
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 1:
            raise ValueError("regulation ordinal must be positive")
        if not isinstance(self.signal, ConversationalSignal) or not isinstance(self.confidence_band, ConfidenceBand) or not isinstance(self.strategy, ExecutedRegulationAction):
            raise ValueError("regulation event fields must be closed values")


@dataclass(frozen=True, slots=True)
class RegulationEvent:
    event_id: str
    session_id: str
    activity_id: str
    turn_id: str
    signal: ConversationalSignal
    confidence_band: ConfidenceBand
    strategy: ExecutedRegulationAction
    ordinal: int
    outcome: RegulationOutcome
    created_at: str


@dataclass(frozen=True, slots=True)
class PersistedTutoringState:
    session: LearningSession
    profile_version: int
    activity_progress: tuple[ActivityProgress, ...] = ()
    regulation_revision: int = 0
    consecutive_regulation_turns: int = 0
    activity_sequence: int = 0
    pending_regulation_event: PendingRegulationEvent | None = None

    def __post_init__(self) -> None:
        progress = tuple(self.activity_progress)
        if not all(isinstance(item, ActivityProgress) for item in progress):
            raise TypeError("activity progress must contain ActivityProgress values")
        if len({item.activity_id for item in progress}) != len(progress):
            raise ValueError("activity progress ids must be unique")
        object.__setattr__(self, "activity_progress", progress)
        for name in ("regulation_revision", "consecutive_regulation_turns", "activity_sequence"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")

    def progress_for(self, activity_id: str) -> ActivityProgress | None:
        return next((item for item in self.activity_progress if item.activity_id == activity_id), None)


@dataclass(frozen=True, slots=True)
class ActivityProgress:
    """Authoritative, optimistic-locked pedagogical counters."""

    activity_id: str
    attempts_used: int
    hints_used: int
    difficulty: int
    consecutive_correct: int = 0
    consecutive_incorrect: int = 0
    version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.activity_id, str) or not self.activity_id.strip():
            raise ValueError("activity id must be nonempty")
        for name in ("attempts_used", "hints_used", "consecutive_correct", "consecutive_incorrect"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if isinstance(self.difficulty, bool) or not isinstance(self.difficulty, int):
            raise ValueError("difficulty must be an integer")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("progress version must be positive")


@dataclass(frozen=True, slots=True)
class ActivityProgressExpectation:
    activity_id: str
    version: int


@dataclass(frozen=True, slots=True)
class TutoringEvent:
    kind: str
    session_id: str
    objective_id: str | None = None
    activity_id: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class RegulationMutation:
    next_revision: int
    consecutive_turns: int
    activity_sequence: int
    executed_action: ExecutedRegulationAction
    pending_event: PendingRegulationEvent | None = None
    close_outcome: RegulationOutcome | None = None

    def __post_init__(self) -> None:
        if isinstance(self.next_revision, bool) or not isinstance(self.next_revision, int) or self.next_revision < 1:
            raise ValueError("next regulation revision must be positive")
        if isinstance(self.consecutive_turns, bool) or not isinstance(self.consecutive_turns, int) or self.consecutive_turns < 0:
            raise ValueError("consecutive regulation turns must be nonnegative")
        if isinstance(self.activity_sequence, bool) or not isinstance(self.activity_sequence, int) or self.activity_sequence < 0:
            raise ValueError("activity sequence must be nonnegative")
        if not isinstance(self.executed_action, ExecutedRegulationAction):
            raise ValueError("executed regulation action must be typed")
        if self.pending_event is not None and self.pending_event.ordinal != self.activity_sequence:
            raise ValueError("pending event ordinal must match activity sequence")
        if self.close_outcome is not None and not isinstance(self.close_outcome, RegulationOutcome):
            raise ValueError("regulation outcome must be typed")


@dataclass(frozen=True, slots=True)
class LearnerSupportReceipt:
    """Transcript-free canonical result of one learner support turn."""

    session_id: str
    turn_id: str
    activity_id: str
    action: str
    speech: str

    def __post_init__(self) -> None:
        for name in ("session_id", "turn_id", "activity_id", "speech"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name.replace('_', ' ')} must be nonempty")
        if self.action not in {
            "hint", "repeat", "simplify-language", "cap-choice",
        }:
            raise ValueError("support action must be a closed canonical action")


@dataclass(frozen=True, slots=True)
class StoredActivity:
    activity_id: str
    activity: Activity


@dataclass(frozen=True, slots=True)
class StoredObservation:
    """Canonical durable observation plus its optimistic-lock version."""

    observation: Observation
    version: int


@dataclass(frozen=True, slots=True)
class ObservationExpectation:
    observation_id: str
    version: int


@dataclass(frozen=True, slots=True)
class MutationBatch:
    """One indivisible durable mutation and its idempotent result."""

    command_id: str
    command_fingerprint: str
    session_id: str
    expected_session_version: int
    expected_profile_version: int
    result: CommandResult
    session: LearningSession | None = None
    activities: tuple[StoredActivity, ...] = ()
    observations: tuple[Observation, ...] = ()
    evidence: tuple[EvidenceRecord, ...] = ()
    profile_change_proposals: tuple[ProposedProfileChange, ...] = ()
    events: tuple[TutoringEvent, ...] = ()
    expected_observations: tuple[ObservationExpectation, ...] = ()
    activity_progress: tuple[ActivityProgress, ...] = ()
    expected_activity_progress: tuple[ActivityProgressExpectation, ...] = ()
    expected_absent_activity_ids: tuple[str, ...] = ()
    support_receipts: tuple[LearnerSupportReceipt, ...] = ()
    expected_regulation_revision: int | None = None
    regulation_mutation: RegulationMutation | None = None

    def __post_init__(self) -> None:
        """Reject ambiguous writes before they reach the durable fence."""

        activity_ids = tuple(item.activity_id for item in self.activities)
        progress_ids = tuple(item.activity_id for item in self.activity_progress)
        expected_progress_ids = tuple(
            item.activity_id for item in self.expected_activity_progress
        )
        absent_ids = tuple(self.expected_absent_activity_ids)
        for name, values in (
            ("activity", activity_ids),
            ("activity progress", progress_ids),
            ("expected activity progress", expected_progress_ids),
            ("expected absent activity", absent_ids),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"{name} ids must be unique")
        if not all(isinstance(value, str) and value.strip() for value in absent_ids):
            raise ValueError("expected absent activity ids must be nonempty strings")
        if self.expected_regulation_revision is not None and (
            isinstance(self.expected_regulation_revision, bool)
            or not isinstance(self.expected_regulation_revision, int)
            or self.expected_regulation_revision < 0
        ):
            raise ValueError("expected regulation revision must be nonnegative")
        if self.regulation_mutation is not None:
            if self.expected_regulation_revision is None:
                raise ValueError("regulation mutation requires expected revision")
            if self.regulation_mutation.next_revision != self.expected_regulation_revision + 1:
                raise ValueError("regulation mutation must advance revision by one")


class CommitOutcome(Enum):
    APPLIED = "applied"
    REPLAYED = "replayed"
    COLLISION = "collision"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class CommitDecision:
    outcome: CommitOutcome
    result: CommandResult | None = None
    reason: str | None = None
    stored_fingerprint: str | None = None

    @classmethod
    def applied(cls, result: CommandResult, fingerprint: str) -> "CommitDecision":
        return cls(CommitOutcome.APPLIED, result=result, stored_fingerprint=fingerprint)

    @classmethod
    def replayed(cls, result: CommandResult, fingerprint: str) -> "CommitDecision":
        return cls(CommitOutcome.REPLAYED, result=result, stored_fingerprint=fingerprint)

    @classmethod
    def collision(cls, result: CommandResult, fingerprint: str) -> "CommitDecision":
        return cls(CommitOutcome.COLLISION, result=result, stored_fingerprint=fingerprint)

    @classmethod
    def conflict(cls, reason: str) -> "CommitDecision":
        return cls(CommitOutcome.CONFLICT, reason=reason)


class TutoringRepository(Protocol):
    """Adapter contract for the durable mutation fence.

    ``commit_once`` performs, in one transaction: command-id lookup and
    fingerprint comparison, all expected-version checks (including observation
    snapshots and activity-progress counters), every write in ``MutationBatch``,
    absence checks for ``expected_absent_activity_ids``, and storage of its
    result. An expected activity that already exists conflicts with reason
    ``activity-id-already-exists``.
    It returns APPLIED for a new write, REPLAYED only for the same fingerprint,
    COLLISION for the same id with a different fingerprint, or CONFLICT for a
    failed optimistic-lock expectation. No partial write may escape.
    """

    def load_command_result(
        self, command_id: str
    ) -> StoredCommandResult | None: ...

    def load_support_receipt(
        self, session_id: str, turn_id: str
    ) -> LearnerSupportReceipt | None: ...

    def load_state(self, session_id: str) -> PersistedTutoringState | None: ...

    def load_activity(
        self, session_id: str, activity_id: str
    ) -> Activity | None: ...

    def load_observation(
        self, session_id: str, observation_id: str
    ) -> StoredObservation | None: ...

    def load_evidence(
        self, learner_id: str, objective_id: str
    ) -> tuple[EvidenceRecord, ...]: ...

    def load_estimate(
        self, learner_id: str, objective_id: str
    ) -> SkillEstimate | None: ...

    def load_regulation_events(self, session_id: str) -> tuple[RegulationEvent, ...]: ...

    def commit_once(self, batch: MutationBatch) -> CommitDecision: ...


class ProvisioningRepository(Protocol):
    """Atomic durable boundary for therapist-authorised provisioning."""

    def create_learner_profile(self, learner: "LearnerProfile", *, curriculum_snapshot: str, curriculum_version: str) -> None: ...
    def load_learner_profile(self, learner_id: str) -> "LearnerProfile | None": ...
    def create_provisioned_plan(self, value: "ProvisionedPlan") -> None: ...
    def update_provisioned_plan(self, value: "ProvisionedPlan", *, expected_version: int) -> bool: ...
    def load_current_provisioned_plan(self, learner_id: str) -> "ProvisionedPlan | None": ...
    def load_profile_version(self, learner_id: str) -> int | None: ...
    def save_audio_consent(self, consent: "AudioConsent") -> None: ...
    def load_audio_consent(self, consent_id: str) -> "AudioConsent | None": ...
    def revoke_audio_consent(self, consent_id: str, learner_id: str, at: datetime) -> "AudioConsent | None": ...
    def create_provisioned_session(self, session: LearningSession, *, expected_plan_id: str, expected_plan_version: int, expected_profile_version: int, join_code_hash: str, join_expires_at: datetime, consent_id: str | None) -> str | None: ...
    def list_consent_session_ids(self, consent_id: str) -> tuple[str, ...]: ...


class ClipPurgePort(Protocol):
    """Idempotent purge contract implemented physically in Task 12."""

    def purge_consent_scope(self, consent_id: str, session_ids: tuple[str, ...]) -> None: ...


class ProvisioningConflict(ValueError):
    """An authoritative provisioning expectation failed inside the fence."""


@dataclass(frozen=True, slots=True)
class StoredCommandResult:
    command_fingerprint: str
    result: CommandResult
