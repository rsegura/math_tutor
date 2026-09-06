from datetime import datetime, timezone
from pathlib import Path

import pytest

from math_tutor.application.next_objectives import NextObjectiveDecision, NextObjectiveError, NextObjectiveService, ProposalDecisionStatus
from math_tutor.application.provisioning import CreateLearner, CreateLearningPlan, ProvisioningService, SessionLimits, StartLearningSession
from math_tutor.domain.activities import Activity, StructuredAnswer
from math_tutor.domain.evidence import EvidenceRecord, Observation, ObservationOutcome, TranscriptionReliabilityPolicy
from math_tutor.domain.learning import CompetencyState, SkillEstimate
from math_tutor.domain.templates import ExpectedAnswerKind
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs
from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository, _dump


class NoClips:
    def purge_consent_scope(self,consent_id,session_ids): pass


def setup(tmp_path):
    database=tmp_path/"next.db"; migrate(database); repo=SQLiteTutoringRepository(database)
    root=Path("src/math_tutor/curricula"); curriculum,_=load_curriculum_catalogs(root/"primary-math-v1.yaml",root/"activity-templates-v1.yaml")
    provisioning=ProvisioningService(repo,curriculum,NoClips())
    provisioning.create_learner(CreateLearner("learner-next","Mar",8))
    provisioning.create_learning_plan(CreateLearningPlan("plan-next","learner-next",("count-to-20",),(),SessionLimits(10,3)))
    session=provisioning.start_learning_session(StartLearningSession("learner-next"))
    activity=Activity("count-items","count-to-20",1,"Cuenta",{"count":4},StructuredAnswer.evaluable(ExpectedAnswerKind.INTEGER,{"answer":4}),(),())
    observation=Observation("observation-next","learner-next",session.tutoring_session_id,"count-to-20","activity-next",ObservationOutcome.CORRECT,.95,0,TranscriptionReliabilityPolicy(.7),"cuatro")
    with repo._connect() as db:
        db.execute("INSERT INTO activities(session_id,activity_id,objective_id,activity_json) VALUES(?,?,?,?)",(session.tutoring_session_id,"activity-next","count-to-20",_dump(activity)))
        db.execute("INSERT INTO observations(observation_id,session_id,learner_id,objective_id,activity_id,observation_json,version) VALUES(?,?,?,?,?,?,1)",(observation.observation_id,session.tutoring_session_id,"learner-next","count-to-20","activity-next",_dump(observation)))
        db.execute("INSERT INTO evidence_records(evidence_id,observation_id,learner_id,session_id,objective_id,reason_for_retention) VALUES(?,?,?,?,?,?)",("evidence-next",observation.observation_id,"learner-next",session.tutoring_session_id,"count-to-20","progression"))
    repo.save_estimate(SkillEstimate("learner-next","count-to-20",CompetencyState.INDEPENDENT,2,("evidence-next",),(observation.observation_id,)))
    return repo,curriculum,session


def test_proposal_approval_is_atomic_idempotent_and_preserves_append_only_history(tmp_path):
    repo,curriculum,session=setup(tmp_path); service=NextObjectiveService(repo,curriculum)
    proposal=service.propose_available("learner-next",session.tutoring_session_id)
    assert proposal.objective_id=="number-sequence-within-20"
    command=NextObjectiveDecision("approve-next",proposal.proposal_id,"learner-next",ProposalDecisionStatus.APPROVED,"Adecuado",0,1,datetime.now(timezone.utc))
    approved=service.decide(command)
    assert approved.status is ProposalDecisionStatus.APPROVED and approved.revision==1
    assert repo.load_current_provisioned_plan("learner-next").version==2
    assert "number-sequence-within-20" in repo.load_current_provisioned_plan("learner-next").plan.authorised_objective_ids
    assert service.decide(command)==approved
    history=repo.list_next_objective_decisions(proposal.proposal_id)
    assert [(item.revision,item.status,item.reason,item.resulting_plan_version) for item in history]==[(1,ProposalDecisionStatus.APPROVED,"Adecuado",2)]
    with pytest.raises(NextObjectiveError,match="command-id-collision"):
        service.decide(NextObjectiveDecision("approve-next",proposal.proposal_id,"learner-next",ProposalDecisionStatus.APPROVED,"Otro",0,1,datetime.now(timezone.utc)))
    with repo._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM next_objective_proposals").fetchone()[0]==1
        assert db.execute("SELECT COUNT(*) FROM next_objective_decisions").fetchone()[0]==1
        with pytest.raises(Exception): db.execute("UPDATE next_objective_proposals SET rationale='cambiado' WHERE proposal_id=?",(proposal.proposal_id,))
        with pytest.raises(Exception): db.execute("DELETE FROM next_objective_decisions WHERE proposal_id=?",(proposal.proposal_id,))


def test_reject_keeps_plan_and_concurrent_stale_decision_fails(tmp_path):
    repo,curriculum,session=setup(tmp_path); service=NextObjectiveService(repo,curriculum)
    proposal=service.propose_available("learner-next",session.tutoring_session_id)
    rejected=service.decide(NextObjectiveDecision("reject-next",proposal.proposal_id,"learner-next",ProposalDecisionStatus.REJECTED,"Esperar",0,1,datetime.now(timezone.utc)))
    assert rejected.status is ProposalDecisionStatus.REJECTED
    assert repo.load_current_provisioned_plan("learner-next").version==1
    with pytest.raises(NextObjectiveError,match="stale-proposal-revision"):
        service.decide(NextObjectiveDecision("other",proposal.proposal_id,"learner-next",ProposalDecisionStatus.APPROVED,"Ahora",0,1,datetime.now(timezone.utc)))
