"""Inward-facing persistence contracts for the tutoring application."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from math_tutor.application.results import CommandResult
from math_tutor.domain.activities import Activity
from math_tutor.domain.evidence import EvidenceRecord, Observation
from math_tutor.domain.learning import (
    LearningSession,
    ProposedProfileChange,
    SkillEstimate,
)


@dataclass(frozen=True, slots=True)
class PersistedTutoringState:
    session: LearningSession
    profile_version: int
    activity_progress: tuple[ActivityProgress, ...] = ()

    def __post_init__(self) -> None:
        progress = tuple(self.activity_progress)
        if not all(isinstance(item, ActivityProgress) for item in progress):
            raise TypeError("activity progress must contain ActivityProgress values")
        if len({item.activity_id for item in progress}) != len(progress):
            raise ValueError("activity progress ids must be unique")
        object.__setattr__(self, "activity_progress", progress)

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

    def commit_once(self, batch: MutationBatch) -> CommitDecision: ...


@dataclass(frozen=True, slots=True)
class StoredCommandResult:
    command_fingerprint: str
    result: CommandResult
