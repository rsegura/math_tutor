"""Versioned therapist corrections over immutable tutoring observations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Protocol

from math_tutor.application.summary import SessionSummarySource, SummaryService
from math_tutor.domain.evidence import EvidenceRecord
from math_tutor.domain.learning import CompetencyState, ProgressionPolicy, SkillEstimate


class ReviewStatus(Enum):
    APPLIED = "applied"
    REPLAYED = "replayed"
    COLLISION = "collision"
    CONFLICT = "conflict"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class DiscardEvidence:
    command_id: str
    command_fingerprint: str
    review_id: str
    learner_id: str
    session_id: str
    evidence_id: str
    reason: str
    expected_review_version: int
    expected_profile_version: int


@dataclass(frozen=True, slots=True)
class CorrectSkillEstimate:
    command_id: str
    command_fingerprint: str
    review_id: str
    learner_id: str
    session_id: str
    objective_id: str
    corrected_state: CompetencyState
    reason: str
    expected_review_version: int
    expected_profile_version: int


@dataclass(frozen=True, slots=True)
class DiscardEvidenceReview:
    evidence_id: str
    evidence_source_session_id: str
    reason: str
    original_interpretation: str | None
    original_outcome: str


@dataclass(frozen=True, slots=True)
class CorrectSkillEstimateReview:
    objective_id: str
    original_from_state: CompetencyState
    original_proposed_state: CompetencyState
    original_evidence_ids: tuple[str, ...]
    original_policy_version: str
    corrected_state: CompetencyState
    reason: str


@dataclass(frozen=True, slots=True)
class ProfileRecalculation:
    objective_id: str
    prior_state: CompetencyState
    recalculated_state: CompetencyState
    excluded_evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReviewResult:
    status: ReviewStatus
    reason: str
    review_id: str
    review_version: int | None = None
    estimate: SkillEstimate | None = None


@dataclass(frozen=True, slots=True)
class ReviewCommandIdentity:
    command_id: str
    command_fingerprint: str
    review_id: str
    learner_id: str
    session_id: str
    source_session_id: str
    action_kind: str
    target_id: str
    reason: str
    expected_review_version: int
    expected_profile_version: int
    corrected_state: str | None


@dataclass(frozen=True, slots=True)
class ReviewMutation:
    command_id: str
    command_fingerprint: str
    review_id: str
    review_version: int
    learner_id: str
    session_id: str
    expected_profile_version: int
    expected_estimate_version: int
    review: DiscardEvidenceReview | CorrectSkillEstimateReview
    estimate: SkillEstimate
    profile_revision_id: str
    profile_revision: ProfileRecalculation
    policy_version: str
    result: ReviewResult

    @property
    def identity(self) -> ReviewCommandIdentity:
        if isinstance(self.review, DiscardEvidenceReview):
            action_kind = "discard-evidence"
            target_id = self.review.evidence_id
        else:
            action_kind = "correct-skill-estimate"
            target_id = self.review.objective_id
        return ReviewCommandIdentity(
            self.command_id, self.command_fingerprint, self.review_id,
            self.learner_id, self.session_id,
            (self.review.evidence_source_session_id
             if isinstance(self.review, DiscardEvidenceReview)
             else self.session_id),
            action_kind, target_id, self.review.reason,
            self.review_version - 1, self.expected_profile_version,
            (self.review.corrected_state.value
             if isinstance(self.review, CorrectSkillEstimateReview)
             else None),
        )


class ReviewCommitPort(Protocol):
    def load_summary_source(self, session_id: str) -> SessionSummarySource | None: ...
    def resolve_review_command(self, identity: ReviewCommandIdentity) -> ReviewResult | None: ...
    def commit_review_once(self, mutation: ReviewMutation) -> ReviewResult: ...


def _canonical_command_fingerprint(
    command: DiscardEvidence | CorrectSkillEstimate,
    *,
    source_session_id: str,
) -> str:
    """Bind idempotency to the full server-understood command contract."""
    common = {
        "schema": "therapist-review-command/v1",
        "command_id": command.command_id,
        "review_id": command.review_id,
        "learner_id": command.learner_id,
        "session_id": command.session_id,
        "source_session_id": source_session_id,
        "reason": command.reason,
        "expected_review_version": command.expected_review_version,
        "expected_profile_version": command.expected_profile_version,
    }
    if isinstance(command, DiscardEvidence):
        payload = {
            **common,
            "kind": "discard-evidence",
            "evidence_id": command.evidence_id,
        }
    else:
        payload = {
            **common,
            "kind": "correct-skill-estimate",
            "objective_id": command.objective_id,
            "corrected_state": command.corrected_state.value,
        }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"therapist-review-command/v1:{hashlib.sha256(canonical).hexdigest()}"


def _command_identity(
    command: DiscardEvidence | CorrectSkillEstimate,
    fingerprint: str,
    *,
    source_session_id: str,
) -> ReviewCommandIdentity:
    if isinstance(command, DiscardEvidence):
        action_kind, target_id = "discard-evidence", command.evidence_id
    else:
        action_kind, target_id = "correct-skill-estimate", command.objective_id
    return ReviewCommandIdentity(
        command.command_id, fingerprint, command.review_id, command.learner_id,
        command.session_id, source_session_id, action_kind, target_id,
        command.reason, command.expected_review_version,
        command.expected_profile_version,
        (command.corrected_state.value
         if isinstance(command, CorrectSkillEstimate)
         else None),
    )


def _recalculate(
    learner_id: str,
    objective_id: str,
    evidence: tuple[EvidenceRecord, ...],
    policy: ProgressionPolicy,
    *,
    next_version: int,
) -> SkillEstimate:
    estimate = SkillEstimate(learner_id, objective_id, CompetencyState.NOT_OBSERVED)
    while True:
        proposal = policy.propose_change(estimate, evidence)
        if proposal is None:
            break
        estimate = estimate.apply(proposal)
        if estimate.state is CompetencyState.NEEDS_REVIEW:
            break
    return SkillEstimate(
        learner_id, objective_id, estimate.state, next_version,
        estimate.supporting_evidence_ids, estimate.supporting_observation_ids,
    )


class TherapistReviewService:
    def __init__(self, repository: ReviewCommitPort, policy: ProgressionPolicy):
        self._repository = repository
        self._policy = policy

    def discard_evidence(self, command: DiscardEvidence) -> ReviewResult:
        source = self._repository.load_summary_source(command.session_id)
        if source is None:
            probe_fingerprint = _canonical_command_fingerprint(
                command, source_session_id=command.session_id
            )
            prior = self._repository.resolve_review_command(
                _command_identity(
                    command, probe_fingerprint,
                    source_session_id=command.session_id,
                )
            )
            if prior is not None:
                return prior
            return ReviewResult(ReviewStatus.REJECTED, "session-not-found", command.review_id)
        SummaryService.validate_source(source, requested_session_id=command.session_id)
        record = next((item for item in source.evidence if item.evidence_id == command.evidence_id), None)
        source_session_id = (
            record.observation.session_id if record is not None else command.session_id
        )
        command_fingerprint = _canonical_command_fingerprint(
            command, source_session_id=source_session_id
        )
        prior = self._repository.resolve_review_command(
            _command_identity(
                command, command_fingerprint,
                source_session_id=source_session_id,
            )
        )
        if prior is not None:
            return prior
        if record is None or source.learner_id != command.learner_id:
            return ReviewResult(ReviewStatus.REJECTED, "evidence-owner-mismatch", command.review_id)
        if command.evidence_id in source.discarded_evidence_ids:
            return ReviewResult(
                ReviewStatus.REJECTED, "evidence-already-discarded", command.review_id
            )
        current = next(
            (item for item in source.estimates if item.objective_id == record.observation.objective_id),
            SkillEstimate(command.learner_id, record.observation.objective_id, CompetencyState.NOT_OBSERVED),
        )
        remaining = tuple(
            item for item in source.evidence
            if item.observation.objective_id == record.observation.objective_id
            and item.evidence_id not in {*source.discarded_evidence_ids, command.evidence_id}
        )
        estimate = _recalculate(
            command.learner_id, record.observation.objective_id, remaining,
            self._policy, next_version=current.version + 1,
        )
        review_version = command.expected_review_version + 1
        result = ReviewResult(
            ReviewStatus.APPLIED, "evidence-discarded", command.review_id,
            review_version, estimate,
        )
        mutation = ReviewMutation(
            command.command_id, command_fingerprint, command.review_id,
            review_version, command.learner_id, command.session_id,
            command.expected_profile_version, current.version,
            DiscardEvidenceReview(
                command.evidence_id, record.observation.session_id, command.reason, record.current_interpretation,
                record.observation.outcome.value,
            ),
            estimate, f"{command.command_id}:profile", ProfileRecalculation(
                record.observation.objective_id, current.state, estimate.state,
                (command.evidence_id,),
            ), self._policy.version, result,
        )
        return self._repository.commit_review_once(mutation)

    def correct_skill_estimate(self, command: CorrectSkillEstimate) -> ReviewResult:
        command_fingerprint = _canonical_command_fingerprint(
            command, source_session_id=command.session_id
        )
        prior = self._repository.resolve_review_command(
            _command_identity(
                command, command_fingerprint,
                source_session_id=command.session_id,
            )
        )
        if prior is not None:
            return prior
        source = self._repository.load_summary_source(command.session_id)
        if source is None:
            return ReviewResult(ReviewStatus.REJECTED, "session-not-found", command.review_id)
        SummaryService.validate_source(source, requested_session_id=command.session_id)
        if source.learner_id != command.learner_id:
            return ReviewResult(ReviewStatus.REJECTED, "session-owner-mismatch", command.review_id)
        current = next(
            (item for item in source.estimates if item.objective_id == command.objective_id),
            None,
        )
        proposal = next(
            (item for item in reversed(source.proposals)
             if item.objective_id == command.objective_id
             and item.estimate_version == (current.version if current else -1)),
            None,
        )
        if current is None or proposal is None:
            return ReviewResult(ReviewStatus.REJECTED, "current-proposal-not-found", command.review_id)
        estimate = SkillEstimate(
            command.learner_id, command.objective_id, command.corrected_state,
            current.version + 1, current.supporting_evidence_ids,
            current.supporting_observation_ids,
        )
        review_version = command.expected_review_version + 1
        result = ReviewResult(
            ReviewStatus.APPLIED, "skill-estimate-corrected", command.review_id,
            review_version, estimate,
        )
        mutation = ReviewMutation(
            command.command_id, command_fingerprint, command.review_id,
            review_version, command.learner_id, command.session_id,
            command.expected_profile_version, current.version,
            CorrectSkillEstimateReview(
                command.objective_id, proposal.from_state, proposal.to_state,
                proposal.evidence_ids, proposal.policy_version,
                command.corrected_state, command.reason,
            ), estimate, f"{command.command_id}:profile",
            ProfileRecalculation(
                command.objective_id, current.state, command.corrected_state, (),
            ), self._policy.version, result,
        )
        return self._repository.commit_review_once(mutation)
