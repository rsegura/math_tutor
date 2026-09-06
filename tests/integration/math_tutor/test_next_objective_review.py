from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread
import shutil

import pytest

from math_tutor.application.next_objectives import NextObjectiveDecision, NextObjectiveError, NextObjectiveService, ProposalDecisionStatus
from math_tutor.application.provisioning import CreateLearner, CreateLearningPlan, ProvisioningService, SessionLimits, StartLearningSession
from math_tutor.domain.activities import Activity, StructuredAnswer
from math_tutor.domain.evidence import EvidenceRecord, Observation, ObservationOutcome, TranscriptionReliabilityPolicy
from math_tutor.domain.learning import CompetencyState, SkillEstimate
from math_tutor.domain.learning import AssistanceThreshold, ProgressionPolicy
from math_tutor.application.review import DiscardEvidence, ReviewStatus, TherapistReviewService
from math_tutor.domain.templates import ExpectedAnswerKind
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs
from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository, _dump


class NoClips:
    def purge_consent_scope(self,consent_id,session_ids): pass


def setup(tmp_path, *, migration_dir=None):
    database=tmp_path/"next.db"; migrate(database,migration_dir=migration_dir); repo=SQLiteTutoringRepository(database)
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


def add_fresh_independent_evidence(repo,session, *, suffix="fresh"):
    activity_id=f"activity-{suffix}"; observation_id=f"observation-{suffix}"; evidence_id=f"evidence-{suffix}"
    activity=Activity("count-items","count-to-20",1,"Cuenta",{"count":5},StructuredAnswer.evaluable(ExpectedAnswerKind.INTEGER,{"answer":5}),(),())
    observation=Observation(observation_id,"learner-next",session.tutoring_session_id,"count-to-20",activity_id,ObservationOutcome.CORRECT,.98,0,TranscriptionReliabilityPolicy(.7),"cinco")
    with repo._connect() as db:
        db.execute("INSERT INTO activities(session_id,activity_id,objective_id,activity_json) VALUES(?,?,?,?)",(session.tutoring_session_id,activity_id,"count-to-20",_dump(activity)))
        db.execute("INSERT INTO observations(observation_id,session_id,learner_id,objective_id,activity_id,observation_json,version) VALUES(?,?,?,?,?,?,1)",(observation_id,session.tutoring_session_id,"learner-next","count-to-20",activity_id,_dump(observation)))
        db.execute("INSERT INTO evidence_records(evidence_id,observation_id,learner_id,session_id,objective_id,reason_for_retention) VALUES(?,?,?,?,?,?)",(evidence_id,observation_id,"learner-next",session.tutoring_session_id,"count-to-20","progression"))
    current=repo.load_estimate("learner-next","count-to-20")
    repo.save_estimate(SkillEstimate("learner-next","count-to-20",CompetencyState.INDEPENDENT,current.version+1,(evidence_id,),(observation_id,)),expected_version=current.version)
    return evidence_id


def test_proposal_approval_is_atomic_idempotent_and_preserves_append_only_history(tmp_path):
    repo,curriculum,session=setup(tmp_path); service=NextObjectiveService(repo,curriculum)
    proposal=service.propose_available("learner-next",session.tutoring_session_id)
    assert proposal.objective_id=="number-sequence-within-20"
    command=NextObjectiveDecision("approve-next",proposal.proposal_id,"learner-next",ProposalDecisionStatus.APPROVED,"Adecuado",0,1,1,datetime.now(timezone.utc))
    approved=service.decide(command)
    assert approved.status is ProposalDecisionStatus.APPROVED and approved.revision==1
    assert repo.load_current_provisioned_plan("learner-next").version==2
    assert "number-sequence-within-20" in repo.load_current_provisioned_plan("learner-next").plan.authorised_objective_ids
    assert service.decide(command)==approved
    history=repo.list_next_objective_decisions(proposal.proposal_id)
    assert [(item.revision,item.status,item.reason,item.resulting_plan_version) for item in history]==[(1,ProposalDecisionStatus.APPROVED,"Adecuado",2)]
    with pytest.raises(NextObjectiveError,match="command-id-collision"):
        service.decide(NextObjectiveDecision("approve-next",proposal.proposal_id,"learner-next",ProposalDecisionStatus.APPROVED,"Otro",0,1,1,datetime.now(timezone.utc)))
    with pytest.raises(NextObjectiveError,match="command-id-collision"):
        service.decide(NextObjectiveDecision("approve-next",proposal.proposal_id,"learner-next",ProposalDecisionStatus.APPROVED,"Adecuado",0,1,2,datetime.now(timezone.utc)))
    with repo._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM next_objective_proposals").fetchone()[0]==1
        assert db.execute("SELECT COUNT(*) FROM next_objective_decisions").fetchone()[0]==1
        with pytest.raises(Exception): db.execute("UPDATE next_objective_proposals SET rationale='cambiado' WHERE proposal_id=?",(proposal.proposal_id,))
        with pytest.raises(Exception): db.execute("DELETE FROM next_objective_decisions WHERE proposal_id=?",(proposal.proposal_id,))


def test_reject_keeps_plan_and_concurrent_stale_decision_fails(tmp_path):
    repo,curriculum,session=setup(tmp_path); service=NextObjectiveService(repo,curriculum)
    proposal=service.propose_available("learner-next",session.tutoring_session_id)
    rejected=service.decide(NextObjectiveDecision("reject-next",proposal.proposal_id,"learner-next",ProposalDecisionStatus.REJECTED,"Esperar",0,1,1,datetime.now(timezone.utc)))
    assert rejected.status is ProposalDecisionStatus.REJECTED
    assert repo.load_current_provisioned_plan("learner-next").version==1
    with pytest.raises(NextObjectiveError,match="stale-proposal-revision"):
        service.decide(NextObjectiveDecision("other",proposal.proposal_id,"learner-next",ProposalDecisionStatus.APPROVED,"Ahora",0,1,1,datetime.now(timezone.utc)))


def test_profile_cas_blocks_approval_when_review_changes_evidence_after_validation(tmp_path):
    repo,curriculum,session=setup(tmp_path); other=SQLiteTutoringRepository(repo.database)
    service=NextObjectiveService(repo,curriculum); proposal=service.propose_available("learner-next",session.tutoring_session_id)
    entered,release=Event(),Event(); original=repo.commit_next_objective_decision
    def delayed(decision,new_plan):
        entered.set(); release.wait(5); return original(decision,new_plan)
    repo.commit_next_objective_decision=delayed
    outcome=[]
    command=NextObjectiveDecision("racing-approval",proposal.proposal_id,"learner-next",ProposalDecisionStatus.APPROVED,"Adecuado",0,1,1,datetime.now(timezone.utc))
    def approve():
        try: outcome.append(service.decide(command))
        except NextObjectiveError as error: outcome.append(error)
    thread=Thread(target=approve)
    thread.start(); assert entered.wait(5)
    thresholds=tuple(AssistanceThreshold(state,3) for state in (CompetencyState.EXPLORING,CompetencyState.WITH_INTENSIVE_HELP,CompetencyState.WITH_LIGHT_HELP,CompetencyState.INDEPENDENT,CompetencyState.GENERALIZED))
    review=TherapistReviewService(other,ProgressionPolicy(thresholds)).discard_evidence(DiscardEvidence("discard-race","ignored","review-race","learner-next",session.tutoring_session_id,"evidence-next","STT incorrecto",0,1))
    assert review.status is ReviewStatus.APPLIED
    release.set(); thread.join(5)
    assert len(outcome)==1 and isinstance(outcome[0],NextObjectiveError)
    assert str(outcome[0])=="stale-profile-version"
    assert repo.load_current_provisioned_plan("learner-next").version==1
    assert repo.load_next_objective_proposal(proposal.proposal_id).status is ProposalDecisionStatus.STALE
    assert repo.list_next_objective_decisions(proposal.proposal_id)==()
    add_fresh_independent_evidence(repo,session)
    fresh=service.propose_available("learner-next",session.tutoring_session_id)
    assert fresh.proposal_id != proposal.proposal_id
    assert fresh.source_profile_version==2
    assert fresh.status is ProposalDecisionStatus.PENDING
    assert service.propose_available("learner-next",session.tutoring_session_id)==fresh
    assert [(item.proposal_id,item.status) for item in repo.list_next_objective_proposals("learner-next",source_session_id=session.tutoring_session_id)]==[
        (proposal.proposal_id,ProposalDecisionStatus.STALE),(fresh.proposal_id,ProposalDecisionStatus.PENDING)]


def test_profile_cas_blocks_proposal_created_after_generation_snapshot(tmp_path):
    repo,curriculum,session=setup(tmp_path); other=SQLiteTutoringRepository(repo.database)
    service=NextObjectiveService(repo,curriculum)
    entered,release=Event(),Event(); original=repo.create_next_objective_proposal
    def delayed(proposal):
        entered.set(); release.wait(5); return original(proposal)
    repo.create_next_objective_proposal=delayed
    outcome=[]
    def generate():
        try: outcome.append(service.propose_available("learner-next",session.tutoring_session_id))
        except NextObjectiveError as error: outcome.append(error)
    thread=Thread(target=generate)
    thread.start(); assert entered.wait(5)
    thresholds=tuple(AssistanceThreshold(state,3) for state in (CompetencyState.EXPLORING,CompetencyState.WITH_INTENSIVE_HELP,CompetencyState.WITH_LIGHT_HELP,CompetencyState.INDEPENDENT,CompetencyState.GENERALIZED))
    review=TherapistReviewService(other,ProgressionPolicy(thresholds)).discard_evidence(DiscardEvidence("discard-generation-race","ignored","review-generation-race","learner-next",session.tutoring_session_id,"evidence-next","STT incorrecto",0,1))
    assert review.status is ReviewStatus.APPLIED
    release.set(); thread.join(5)
    assert len(outcome)==1 and isinstance(outcome[0],NextObjectiveError)
    assert str(outcome[0])=="stale-profile-version"
    with repo._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM next_objective_proposals").fetchone()[0]==0


def test_incremental_profile_version_migration_backfills_only_pending_proposals(tmp_path):
    source=Path("src/math_tutor/infrastructure/persistence/migrations"); partial=tmp_path/"through-15"; partial.mkdir()
    for path in source.glob("*.sql"):
        if int(path.name.split("_",1)[0])<=15: shutil.copy(path,partial/path.name)
    repo,_,session=setup(tmp_path,migration_dir=partial)
    with repo._connect() as db:
        values=("legacy-pending","learner-next",session.tutoring_session_id,"plan-next",1,"number-sequence-within-20","Razón",datetime.now(timezone.utc).isoformat())
        db.execute("INSERT INTO next_objective_proposals(proposal_id,learner_id,source_session_id,source_plan_id,source_plan_version,objective_id,rationale,created_at) VALUES(?,?,?,?,?,?,?,?)",values)
        db.execute("INSERT INTO next_objective_proposal_evidence(proposal_id,position,evidence_id,learner_id,evidence_session_id) VALUES(?,?,?,?,?)",("legacy-pending",0,"evidence-next","learner-next",session.tutoring_session_id))
        terminal=("legacy-terminal","learner-next",session.tutoring_session_id,"plan-next",1,"compare-within-20","Razón",datetime.now(timezone.utc).isoformat())
        db.execute("INSERT INTO next_objective_proposals(proposal_id,learner_id,source_session_id,source_plan_id,source_plan_version,objective_id,rationale,created_at) VALUES(?,?,?,?,?,?,?,?)",terminal)
        db.execute("INSERT INTO next_objective_decisions(proposal_id,revision,command_id,learner_id,status,reason,expected_revision,expected_plan_version,resulting_plan_version,decided_at) VALUES(?,?,?,?,?,?,?,?,?,?)",("legacy-terminal",1,"legacy-command","learner-next","rejected","No",0,1,None,datetime.now(timezone.utc).isoformat()))
    migrate(repo.database)
    assert repo.load_next_objective_proposal("legacy-pending").source_profile_version==1
    assert repo.load_next_objective_proposal("legacy-terminal").status is ProposalDecisionStatus.STALE
    with repo._connect() as db:
        assert db.execute("SELECT stale_reason FROM next_objective_proposals WHERE proposal_id='legacy-terminal'").fetchone()[0]=="legacy-profile-version-unverifiable"
