"""Deterministic next-objective proposals and therapist-only decisions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib

from math_tutor.domain.curriculum import CurriculumCatalog
from math_tutor.domain.learning import CompetencyState


class ProposalDecisionStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class NextObjectiveProposal:
    proposal_id: str
    learner_id: str
    source_session_id: str
    source_plan_id: str
    source_plan_version: int
    objective_id: str
    rationale: str
    evidence_ids: tuple[str, ...]
    status: ProposalDecisionStatus
    revision: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        texts=(self.proposal_id,self.learner_id,self.source_session_id,self.source_plan_id,self.objective_id,self.rationale)
        if any(not isinstance(value,str) or not value or value!=value.strip() for value in texts): raise NextObjectiveError("invalid-next-objective-proposal")
        if isinstance(self.source_plan_version,bool) or not isinstance(self.source_plan_version,int) or self.source_plan_version<1: raise NextObjectiveError("invalid-next-objective-proposal")
        if not self.evidence_ids or len(self.evidence_ids)!=len(set(self.evidence_ids)): raise NextObjectiveError("invalid-next-objective-proposal")
        if any(not isinstance(value,str) or not value or value!=value.strip() for value in self.evidence_ids): raise NextObjectiveError("invalid-next-objective-proposal")
        if self.status is ProposalDecisionStatus.PENDING and self.revision!=0: raise NextObjectiveError("invalid-next-objective-proposal")
        if self.status is not ProposalDecisionStatus.PENDING and self.revision<1: raise NextObjectiveError("invalid-next-objective-proposal")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None or self.updated_at<self.created_at: raise NextObjectiveError("invalid-next-objective-proposal")


@dataclass(frozen=True, slots=True)
class NextObjectiveDecision:
    command_id: str
    proposal_id: str
    learner_id: str
    status: ProposalDecisionStatus
    reason: str
    expected_revision: int
    expected_plan_version: int
    decided_at: datetime

    def __post_init__(self) -> None:
        if self.status not in {ProposalDecisionStatus.APPROVED,ProposalDecisionStatus.REJECTED}:
            raise NextObjectiveError("invalid-decision")
        if any(not isinstance(value,str) or not value or value != value.strip() for value in (self.command_id,self.proposal_id,self.learner_id,self.reason)):
            raise NextObjectiveError("invalid-decision")
        if isinstance(self.expected_revision,bool) or not isinstance(self.expected_revision,int) or self.expected_revision < 0:
            raise NextObjectiveError("invalid-expected-revision")
        if isinstance(self.expected_plan_version,bool) or not isinstance(self.expected_plan_version,int) or self.expected_plan_version < 1:
            raise NextObjectiveError("invalid-expected-plan-version")
        if self.decided_at.tzinfo is None: raise NextObjectiveError("decision timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class NextObjectiveDecisionRevision:
    proposal_id: str
    revision: int
    status: ProposalDecisionStatus
    reason: str
    expected_plan_version: int
    resulting_plan_version: int | None
    decided_at: datetime


class NextObjectiveError(ValueError): pass


class NextObjectiveService:
    def __init__(self, repository, curriculum: CurriculumCatalog, *, now=None) -> None:
        self.repository, self.curriculum = repository, curriculum
        self.now = now or (lambda: datetime.now(timezone.utc))

    def propose_available(self, learner_id: str, source_session_id: str) -> NextObjectiveProposal | None:
        aggregate = self.repository.load_session_aggregate(source_session_id)
        plan = self.repository.load_current_provisioned_plan(learner_id)
        if aggregate is None or plan is None or aggregate.session.learner_id != learner_id:
            raise NextObjectiveError("proposal-owner-mismatch")
        if (aggregate.session.plan_id, aggregate.session.plan_version) != (plan.plan.plan_id, plan.version):
            raise NextObjectiveError("stale-source-plan")
        source = self.repository.load_summary_source(source_session_id) if hasattr(self.repository,"load_summary_source") else None
        visible = None if source is None else {item.evidence_id for item in source.evidence} - set(source.discarded_evidence_ids)
        achieved = {item.objective_id for item in aggregate.estimates if item.state in {CompetencyState.INDEPENDENT,CompetencyState.GENERALIZED}
                    and item.supporting_evidence_ids and (visible is None or set(item.supporting_evidence_ids)<=visible)}
        candidate = next((item for item in self.curriculum.objectives
                          if item.id not in plan.plan.authorised_objective_ids
                          and item.prerequisite_ids and self.curriculum.prerequisites_met(item.id, achieved)), None)
        if candidate is None: return None
        evidence_ids = tuple(dict.fromkeys(evidence_id for item in aggregate.estimates
            if item.objective_id in candidate.prerequisite_ids and item.objective_id in achieved
            for evidence_id in item.supporting_evidence_ids))
        if not evidence_ids: return None
        identity = f"{learner_id}\0{source_session_id}\0{plan.plan.plan_id}\0{plan.version}\0{candidate.id}"
        proposal_id = "next-" + hashlib.sha256(identity.encode()).hexdigest()[:32]
        at = self.now()
        proposal = NextObjectiveProposal(proposal_id,learner_id,source_session_id,plan.plan.plan_id,plan.version,
            candidate.id,f"Prerrequisitos consolidados: {', '.join(candidate.prerequisite_ids)}.",evidence_ids,
            ProposalDecisionStatus.PENDING,0,at,at)
        return self.repository.create_next_objective_proposal(proposal)

    def decide(self, decision: NextObjectiveDecision) -> NextObjectiveProposal:
        prior = self.repository.resolve_next_objective_decision(decision) if hasattr(self.repository,"resolve_next_objective_decision") else None
        if prior is not None: return prior
        proposal = self.repository.load_next_objective_proposal(decision.proposal_id)
        if proposal is None or proposal.learner_id != decision.learner_id:
            raise NextObjectiveError("proposal-owner-mismatch")
        if proposal.status is not ProposalDecisionStatus.PENDING or proposal.revision != decision.expected_revision:
            raise NextObjectiveError("stale-proposal-revision")
        plan = self.repository.load_current_provisioned_plan(decision.learner_id)
        if plan is None or plan.version != decision.expected_plan_version or (plan.plan.plan_id,plan.version)!=(proposal.source_plan_id,proposal.source_plan_version):
            raise NextObjectiveError("stale-plan-version")
        next_plan = None
        if decision.status is ProposalDecisionStatus.APPROVED:
            definition = self.curriculum.objective(proposal.objective_id)
            aggregate = self.repository.load_session_aggregate(proposal.source_session_id)
            source = self.repository.load_summary_source(proposal.source_session_id) if hasattr(self.repository,"load_summary_source") else None
            visible = None if source is None else {item.evidence_id for item in source.evidence} - set(source.discarded_evidence_ids)
            achieved = {item.objective_id for item in aggregate.estimates if item.state in {CompetencyState.INDEPENDENT,CompetencyState.GENERALIZED}
                        and item.supporting_evidence_ids and (visible is None or set(item.supporting_evidence_ids)<=visible)}
            if proposal.objective_id in plan.plan.authorised_objective_ids or not self.curriculum.prerequisites_met(definition.id, achieved):
                raise NextObjectiveError("proposal-no-longer-valid")
            if not set(proposal.evidence_ids) <= {evidence_id for item in aggregate.estimates if item.objective_id in definition.prerequisite_ids for evidence_id in item.supporting_evidence_ids}:
                raise NextObjectiveError("proposal-evidence-no-longer-valid")
            updated = replace(plan.plan,authorised_objective_ids=(*plan.plan.authorised_objective_ids,proposal.objective_id),active_objective_ids=(*plan.plan.active_objective_ids,proposal.objective_id),version=plan.version+1)
            next_plan = replace(plan,plan=updated)
        return self.repository.commit_next_objective_decision(decision,next_plan)
