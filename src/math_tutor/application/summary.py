"""Authoritative, evidence-linked session summaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from math_tutor.domain.evidence import EvidenceRecord
from math_tutor.domain.learning import ProposedProfileChange, SkillEstimate


class NarrativeValidationError(ValueError):
    """Raised when optional prose does not match the authoritative summary."""


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
    text: str
    evidence_ids: tuple[str, ...]


class SummaryService:
    def __init__(self, source: SummarySourcePort):
        self._source = source

    def build(self, session_id: str) -> SessionSummary:
        source = self._source.load_summary_source(session_id)
        if source is None:
            raise LookupError("session-summary-source-not-found")
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
            evidence_ids = tuple(item for item in proposal.evidence_ids if item in visible_ids)
            if not evidence_ids:
                continue
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
            if not isinstance(claim.text, str) or not claim.text.strip():
                raise NarrativeValidationError("narrative claim text must be nonempty")
            seen.add(claim.claim_id)
        return tuple(claims)
