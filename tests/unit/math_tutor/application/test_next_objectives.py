from math_tutor.application.next_objectives import NextObjectiveDecision, NextObjectiveError, NextObjectiveService, ProposalDecisionStatus
from math_tutor.domain.curriculum import CurriculumBand, CurriculumCatalog, HintDefinition, LearningObjective
from math_tutor.domain.learning import CompetencyState, LearningPlan, PresentationProfile, SkillEstimate
from math_tutor.application.provisioning import ProvisionedPlan, SessionLimits
import pytest


def objective(identifier, requires=()):
    return LearningObjective(identifier,CurriculumBand.INITIAL,identifier,"Practica",requires,("family",),("error",),(HintDefinition(f"hint-{identifier}","Ayuda",1),))


class Repo:
    def __init__(self): self.created=[]; self.state=CompetencyState.INDEPENDENT; self.proposals={}; self.commits=[]
    def load_current_provisioned_plan(self, learner_id):
        plan=LearningPlan(learner_id,("count",),("count",),PresentationProfile.for_age(8),"plan",2)
        return ProvisionedPlan(plan,(),SessionLimits(10,3))
    def load_session_aggregate(self, session_id):
        session=type("Session",(),{"session_id":session_id,"learner_id":"learner","plan_id":"plan","plan_version":2})()
        estimate=SkillEstimate("learner","count",self.state,2,("evidence-1",),("observation-1",))
        return type("Aggregate",(),{"session":session,"estimates":(estimate,)})()
    def create_next_objective_proposal(self, proposal): self.created.append(proposal); self.proposals[proposal.proposal_id]=proposal; return proposal
    def load_next_objective_proposal(self, proposal_id): return self.proposals.get(proposal_id)
    def commit_next_objective_decision(self, decision, plan): self.commits.append((decision,plan)); return replace(self.proposals[decision.proposal_id],status=decision.status,revision=1,updated_at=decision.decided_at)


def test_deterministic_producer_only_proposes_unlocked_catalog_objectives_with_evidence():
    repo=Repo(); catalog=CurriculumCatalog((objective("count"),objective("place",("count",)),objective("blocked",("place",))))
    proposal=NextObjectiveService(repo,catalog).propose_available("learner","session")
    assert proposal.objective_id=="place"
    assert proposal.evidence_ids==("evidence-1",)
    assert proposal.status is ProposalDecisionStatus.PENDING
    assert repo.created==[proposal]


def test_producer_does_not_propose_when_prerequisites_are_not_independent():
    repo=Repo(); repo.state=CompetencyState.EXPLORING
    catalog=CurriculumCatalog((objective("count"),objective("place",("count",))))
    assert NextObjectiveService(repo,catalog).propose_available("learner","session") is None


def test_approval_adds_objective_in_new_plan_version_but_rejection_does_not():
    repo=Repo(); catalog=CurriculumCatalog((objective("count"),objective("place",("count",))))
    service=NextObjectiveService(repo,catalog); proposal=service.propose_available("learner","session")
    approved=service.decide(NextObjectiveDecision("cmd",proposal.proposal_id,"learner",ProposalDecisionStatus.APPROVED,"Adecuado",0,2,datetime.now(timezone.utc)))
    assert approved.status is ProposalDecisionStatus.APPROVED
    assert repo.commits[0][1].version==3
    assert repo.commits[0][1].plan.authorised_objective_ids==("count","place")
    repo=Repo(); service=NextObjectiveService(repo,catalog); proposal=service.propose_available("learner","session")
    service.decide(NextObjectiveDecision("cmd-r",proposal.proposal_id,"learner",ProposalDecisionStatus.REJECTED,"Aún no",0,2,datetime.now(timezone.utc)))
    assert repo.commits[0][1] is None


def test_decision_rejects_stale_plan_and_cross_owner():
    repo=Repo(); catalog=CurriculumCatalog((objective("count"),objective("place",("count",))))
    service=NextObjectiveService(repo,catalog); proposal=service.propose_available("learner","session")
    for learner,version in (("other",2),("learner",1)):
        with pytest.raises(NextObjectiveError):
            service.decide(NextObjectiveDecision("cmd",proposal.proposal_id,learner,ProposalDecisionStatus.APPROVED,"Motivo",0,version,datetime.now(timezone.utc)))
from dataclasses import replace
from datetime import datetime, timezone
