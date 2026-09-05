"""Authoritative, evidence-linked session summaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from math_tutor.domain.evidence import EvidenceRecord
from math_tutor.domain.learning import ProposedProfileChange, SkillEstimate


class NarrativeValidationError(ValueError):
    """Raised when optional prose does not match the authoritative summary."""


@dataclass(frozen=True, slots=True)
class SummaryActivityRef:
    learner_id: str
    session_id: str
    activity_id: str
    objective_id: str


@dataclass(frozen=True, slots=True)
class SessionSummarySource:
    session_id: str
    learner_id: str
    session_version: int
    profile_version: int
    evidence: tuple[EvidenceRecord, ...]
    estimates: tuple[SkillEstimate, ...]
    proposals: tuple[ProposedProfileChange, ...]
    discarded_evidence_ids: tuple[str, ...] = ()
    authorised_objective_ids: tuple[str, ...] = ()
    activity_refs: tuple[SummaryActivityRef, ...] = ()
    proposal_session_ids: tuple[str, ...] = ()


class SummarySourcePort(Protocol):
    def load_summary_source(self, session_id: str) -> SessionSummarySource | None: ...


@dataclass(frozen=True, slots=True)
class SummaryClaim:
    claim_id: str
    kind: str
    text: str
    evidence_ids: tuple[str, ...]
    is_hypothesis: bool = False


@dataclass(frozen=True, slots=True)
class SessionSummary:
    session_id: str
    learner_id: str
    source_session_version: int
    source_profile_version: int
    claims: tuple[SummaryClaim, ...]
    historical_evidence_ids: tuple[str, ...]
    discarded_evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NarrativeClaim:
    claim_id: str
    kind: str
    is_hypothesis: bool
    text: str
    evidence_ids: tuple[str, ...]


class SummaryService:
    def __init__(self, source: SummarySourcePort):
        self._source = source

    def build(self, session_id: str) -> SessionSummary:
        source = self._source.load_summary_source(session_id)
        if source is None:
            raise LookupError("session-summary-source-not-found")
        self.validate_source(source, requested_session_id=session_id)
        canonical = {record.evidence_id: record for record in source.evidence}
        discarded = frozenset(source.discarded_evidence_ids)
        if not discarded <= canonical.keys():
            raise ValueError("discarded evidence is outside the canonical session")
        visible = tuple(record for record in source.evidence if record.evidence_id not in discarded)
        claims: list[SummaryClaim] = []
        for record in visible:
            observation = record.observation
            if observation.session_id != source.session_id:
                continue
            claims.append(SummaryClaim(
                f"observation:{observation.observation_id}", "observation",
                f"Resultado {observation.outcome.value} con ayuda {observation.assistance_level}.",
                (record.evidence_id,),
            ))
            if record.current_interpretation is not None:
                claims.append(SummaryClaim(
                    f"interpretation:{record.evidence_id}:{len(record.interpretations)}",
                    "interpretation", record.current_interpretation,
                    (record.evidence_id,), True,
                ))
        visible_ids = canonical.keys() - discarded
        for index, proposal in enumerate(source.proposals, start=1):
            if not set(proposal.evidence_ids) <= visible_ids:
                continue
            evidence_ids = proposal.evidence_ids
            claims.append(SummaryClaim(
                f"profile-proposal:{proposal.objective_id}:{index}", "profile-proposal",
                f"Se propone {proposal.to_state.value} para {proposal.objective_id}.",
                evidence_ids, True,
            ))
        return SessionSummary(
            source.session_id, source.learner_id, source.session_version,
            source.profile_version, tuple(claims), tuple(canonical),
            tuple(item for item in canonical if item in discarded),
        )

    @staticmethod
    def validate_source(
        source: SessionSummarySource, *, requested_session_id: str
    ) -> None:
        malformed = ValueError("malformed-summary-source")
        if (
            source.session_id != requested_session_id
            or not isinstance(source.learner_id, str)
            or not source.learner_id.strip()
            or isinstance(source.session_version, bool)
            or not isinstance(source.session_version, int)
            or source.session_version < 1
            or isinstance(source.profile_version, bool)
            or not isinstance(source.profile_version, int)
            or source.profile_version < 1
        ):
            raise malformed
        evidence_by_id = {item.evidence_id: item for item in source.evidence}
        observation_ids = {item.observation.observation_id for item in source.evidence}
        if (
            len(evidence_by_id) != len(source.evidence)
            or len(observation_ids) != len(source.evidence)
        ):
            raise malformed
        authorised = set(source.authorised_objective_ids)
        if not authorised or len(authorised) != len(source.authorised_objective_ids):
            raise malformed
        activity_refs = {
            (item.learner_id, item.session_id, item.activity_id, item.objective_id)
            for item in source.activity_refs
        }
        if len(activity_refs) != len(source.activity_refs):
            raise malformed
        if any(
            item.learner_id != source.learner_id
            or item.objective_id not in authorised
            for item in source.activity_refs
        ):
            raise malformed
        for record in source.evidence:
            observation = record.observation
            if (
                record.learner_id != source.learner_id
                or observation.learner_id != source.learner_id
                or observation.objective_id not in authorised
                or (source.learner_id, observation.session_id, observation.activity_id, observation.objective_id)
                not in activity_refs
            ):
                raise malformed
        for estimate in source.estimates:
            if estimate.learner_id != source.learner_id or estimate.objective_id not in authorised:
                raise malformed
            for evidence_id, observation_id in zip(
                estimate.supporting_evidence_ids,
                estimate.supporting_observation_ids,
                strict=True,
            ):
                record = evidence_by_id.get(evidence_id)
                if (
                    record is None
                    or record.observation.observation_id != observation_id
                    or record.observation.objective_id != estimate.objective_id
                ):
                    raise malformed
        if len({item.objective_id for item in source.estimates}) != len(source.estimates):
            raise malformed
        if (
            len(source.proposal_session_ids) != len(source.proposals)
            or any(item != source.session_id for item in source.proposal_session_ids)
        ):
            raise malformed
        for proposal in source.proposals:
            if proposal.learner_id != source.learner_id or proposal.objective_id not in authorised:
                raise malformed
            for evidence_id, observation_id in zip(
                proposal.evidence_ids, proposal.observation_ids, strict=True
            ):
                record = evidence_by_id.get(evidence_id)
                if (
                    record is None
                    or record.observation.observation_id != observation_id
                    or record.observation.objective_id != proposal.objective_id
                ):
                    raise malformed
        if not set(source.discarded_evidence_ids) <= evidence_by_id.keys():
            raise malformed
        if len(set(source.discarded_evidence_ids)) != len(source.discarded_evidence_ids):
            raise malformed

    def validate_narrative(
        self, summary: SessionSummary, claims: tuple[NarrativeClaim, ...]
    ) -> tuple[NarrativeClaim, ...]:
        authoritative = {claim.claim_id: claim for claim in summary.claims}
        seen: set[str] = set()
        for claim in claims:
            source = authoritative.get(claim.claim_id)
            if source is None or claim.claim_id in seen:
                raise NarrativeValidationError("unknown or duplicate narrative claim id")
            if tuple(claim.evidence_ids) != source.evidence_ids:
                raise NarrativeValidationError("narrative evidence differs from authoritative claim")
            if claim.kind != source.kind or claim.is_hypothesis is not source.is_hypothesis:
                raise NarrativeValidationError("narrative claim classification differs from authoritative claim")
            if not isinstance(claim.text, str) or not claim.text.strip():
                raise NarrativeValidationError("narrative claim text must be nonempty")
            seen.add(claim.claim_id)
        return tuple(claims)
