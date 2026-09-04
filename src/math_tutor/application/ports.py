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
    snapshots), every write in ``MutationBatch``, and storage of its result.
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
