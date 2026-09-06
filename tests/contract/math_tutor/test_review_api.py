from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import wave

from fastapi import FastAPI
from fastapi.testclient import TestClient

from math_tutor.application.review import ReviewResult, ReviewStatus
from math_tutor.application.summary import SessionSummary, SummaryClaim
from math_tutor.domain.learning import CompetencyState, SkillEstimate
from math_tutor.infrastructure.persistence.repositories import EvidenceClipRecord
from web.review_api import create_review_router
from math_tutor.application.next_objectives import NextObjectiveProposal, ProposalDecisionStatus
from math_tutor.application.provisioning import ProvisioningService
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs
from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository
from math_tutor.infrastructure.persistence.repositories import _dump
from math_tutor.infrastructure.clip_retention import ClipRetentionService, RetentionSettings
from math_tutor.infrastructure.evidence_clips import OpaqueClipStore, SelectedAudio
from math_tutor.application.provisioning import CreateLearner, CreateLearningPlan, SessionLimits, StartLearningSession
from math_tutor.domain.activities import Activity, StructuredAnswer
from math_tutor.domain.evidence import Observation, ObservationOutcome, TranscriptionReliabilityPolicy
from math_tutor.domain.templates import ExpectedAnswerKind
from web.app import WebSettings, create_app


TOKEN = "server-secret-value-123456789"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


@dataclass
class Learner:
    learner_id: str = "learner-1"
    pseudonym: str = "Luna"
    age_years: int = 8


class Queries:
    def list_review_learners(self):
        return (Learner(),)

    def list_review_sessions(self, learner_id):
        return ({"session_id": "session-1", "learner_id": learner_id, "active": False, "created_at": "2026-09-05T10:00:00+00:00"},) if learner_id == "learner-1" else ()

    def load_learner_profile(self, learner_id):
        return Learner() if learner_id == "learner-1" else None

    def load_session_aggregate(self, session_id):
        if session_id != "session-1": return None
        session = type("Session", (), {"session_id":"session-1", "learner_id":"learner-1", "authorised_objective_ids":("units-tens",)})()
        return type("Aggregate", (), {
            "session":session,
            "estimates":(SkillEstimate("learner-1", "units-tens", CompetencyState.EXPLORING),),
            "evidence":(), "reviews":(), "clips":(),
        })()
    def list_next_objective_proposals(self,learner_id,source_session_id=None):
        at=datetime(2026,9,5,tzinfo=timezone.utc)
        return (NextObjectiveProposal("next-1",learner_id,source_session_id,"plan-1",2,3,7,"add-within-10","Prerrequisito consolidado",("evidence-1",),ProposalDecisionStatus.PENDING,0,at,at),)


class Summary:
    def build(self, session_id):
        return SessionSummary(session_id, "learner-1", 2, 3, (
            SummaryClaim("observation:o1", "observation", "Resultado correcto con ayuda 1.", ("evidence-1",)),
            SummaryClaim("profile-proposal:units-tens:1", "profile-proposal", "Se propone exploring.", ("evidence-1",), True),
        ), ("evidence-1",), ())


class Reviews:
    def __init__(self): self.commands=[]
    def discard_evidence(self, command):
        self.commands.append(command); return ReviewResult(ReviewStatus.APPLIED, "evidence-discarded", command.review_id, 1)
    def correct_skill_estimate(self, command):
        self.commands.append(command); return ReviewResult(ReviewStatus.CONFLICT, "stale-review-state", command.review_id)


class Clips:
    def __init__(self): self.deleted=[]
    def load(self, clip_id):
        if clip_id != "clip-1234567890123456": return None
        return EvidenceClipRecord(clip_id,"evidence-1","learner-1","session-1",2.5,"clip-1234567890123456.wav",(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),"consent","snapshot",datetime.now(timezone.utc).isoformat())
    def read(self, record): return b"RIFFaudio"
    def delete(self, clip_id, *, learner_id, session_id):
        if (learner_id,session_id)!=("learner-1","session-1"): return None
        if clip_id in self.deleted: return False
        self.deleted.append(clip_id); return True


class NextObjectives:
    def __init__(self): self.decisions=[]
    def propose_available(self,learner_id,session_id): return Queries().list_next_objective_proposals(learner_id,source_session_id=session_id)[0]
    def decide(self,decision): self.decisions.append(decision); return replace(Queries().list_next_objective_proposals(decision.learner_id,source_session_id="session-1")[0],status=decision.status,revision=1,updated_at=decision.decided_at)


def client():
    app=FastAPI(); app.include_router(create_review_router(Queries(), TOKEN, summary_service=Summary(), review_service=Reviews(), clip_access=Clips(), next_objective_service=NextObjectives()))
    return TestClient(app)


def test_review_reads_require_auth_and_return_business_safe_authoritative_data():
    api=client()
    assert api.get("/api/review/learners").status_code == 401
    learners=api.get("/api/review/learners",headers=HEADERS)
    sessions=api.get("/api/review/learners/learner-1/sessions",headers=HEADERS)
    detail=api.get("/api/review/learners/learner-1/sessions/session-1",headers=HEADERS)
    assert learners.json()=={"learners":[{"learner_id":"learner-1","pseudonym":"Luna","age_years":8}]}
    assert sessions.json()["sessions"][0]["session_id"]=="session-1"
    body=detail.json()
    assert body["summary"][0]["evidence_ids"]==["evidence-1"]
    assert body["summary"][1]["status"]=="hypothesis"
    assert body["profile_version"]==3
    assert body["objective_estimates"][0]["state"]=="exploring"
    assert body["objective_estimates"][0]["status"]=="current-estimate"
    assert body["objective_estimates"][0]["support_confidence"]["kind"]=="stt-confidence-range"
    assert body["profile_change_proposals"]==[body["authoritative_claims"][1]]
    assert body["hypotheses"]==[]
    assert body["next_objective_proposals"][0]["objective_id"]=="add-within-10"
    assert body["next_objective_proposals"][0]["status"]=="pending"
    assert body["next_objective_history"]==[]
    assert all(response.headers["cache-control"]=="no-store" for response in (learners,sessions,detail))
    assert "diagnos" not in detail.text.lower()


def test_stale_undecided_next_objective_is_kept_in_history_not_actions():
    class StaleQueries(Queries):
        def list_next_objective_proposals(self,learner_id,source_session_id=None):
            proposal=super().list_next_objective_proposals(learner_id,source_session_id)[0]
            return (replace(proposal,status=ProposalDecisionStatus.STALE),)
        def list_next_objective_decisions(self,proposal_id): return ()
    app=FastAPI(); app.include_router(create_review_router(StaleQueries(),TOKEN,summary_service=Summary(),review_service=Reviews(),clip_access=Clips(),next_objective_service=NextObjectives()))
    body=TestClient(app).get("/api/review/learners/learner-1/sessions/session-1",headers=HEADERS).json()
    assert body["next_objective_proposals"]==[]
    assert body["next_objective_history"][0]["status"]=="stale"
    assert body["next_objective_history"][0]["decision_reason"]=="source-snapshot-superseded"


def test_cross_owner_paths_fail_closed():
    api=client()
    assert api.get("/api/review/learners/other/sessions/session-1",headers=HEADERS).status_code == 404
    assert api.get("/api/review/learners/learner-1/sessions/missing",headers=HEADERS).status_code == 404


def test_clip_access_and_idempotent_deletion_are_scoped_and_no_store():
    api=client(); path="/api/review/learners/learner-1/sessions/session-1/clips/clip-1234567890123456"
    audio=api.get(path,headers=HEADERS)
    assert audio.status_code==200 and audio.content==b"RIFFaudio"
    assert audio.headers["content-type"].startswith("audio/wav")
    assert audio.headers["cache-control"]=="no-store"
    assert api.get(path.replace("learner-1","other"),headers=HEADERS).status_code==404
    assert api.delete(path,headers=HEADERS).json()=={"status":"deleted"}
    assert api.delete(path,headers=HEADERS).json()=={"status":"already-deleted"}


def test_structured_commands_are_strict_versioned_and_map_conflicts():
    api=client(); base="/api/review/learners/learner-1/sessions/session-1/corrections"
    discard={"kind":"discard-evidence","command_id":"cmd-1","review_id":"review-1","evidence_id":"evidence-1","reason":"Transcripción no fiable","expected_review_version":0,"expected_profile_version":3}
    applied=api.post(base,headers=HEADERS,json=discard)
    assert applied.status_code==200 and applied.json()["status"]=="applied"
    correction={"kind":"correct-skill-estimate","command_id":"cmd-2","review_id":"review-2","objective_id":"units-tens","corrected_state":"needs-review","reason":"La ayuda fue mayor","expected_review_version":0,"expected_profile_version":2}
    assert api.post(base,headers=HEADERS,json=correction).status_code==409
    assert api.post(base,headers=HEADERS,json={**discard,"unexpected":True}).status_code==422


def test_next_objective_generation_and_decision_are_authenticated_strict_and_versioned():
    api=client(); base="/api/review/learners/learner-1/sessions/session-1/next-objective-proposals"
    assert api.post(f"{base}/generate").status_code==401
    generated=api.post(f"{base}/generate",headers=HEADERS)
    assert generated.status_code==200 and generated.json()["status"]=="pending"
    body={"command_id":"next-command","decision":"approved","reason":"Objetivo adecuado","expected_revision":0,"expected_plan_version":2,"expected_profile_version":3,"expected_learning_state_version":7}
    decided=api.post(f"{base}/next-1/decision",headers=HEADERS,json=body)
    assert decided.status_code==200 and decided.json()["status"]=="approved"
    assert api.post(f"{base}/next-1/decision",headers=HEADERS,json={**body,"extra":1}).status_code==422


class NoClips:
    def purge_consent_scope(self, consent_id, session_ids): pass


def test_real_query_boundary_lists_only_the_owned_provisioned_session(tmp_path):
    database=tmp_path/"review-real.db"; migrate(database)
    root=__import__("pathlib").Path("src/math_tutor/curricula")
    curriculum,_=load_curriculum_catalogs(root/"primary-math-v1.yaml",root/"activity-templates-v1.yaml")
    repository=SQLiteTutoringRepository(database)
    service=ProvisioningService(repository,curriculum,NoClips())
    api=TestClient(create_app(WebSettings(True,TOKEN),provisioning=service))
    assert api.post("/api/therapist/learners",headers=HEADERS,json={"learner_id":"learner-real","pseudonym":"Sol","age_years":9}).status_code==201
    assert api.post("/api/therapist/learners/learner-real/plans",headers=HEADERS,json={"plan_id":"plan-real","objective_ids":["units-tens"],"adaptations":[],"limits":{"duration_minutes":10,"max_activities":3}}).status_code==201
    started=api.post("/api/therapist/learners/learner-real/sessions",headers=HEADERS,json={}).json()
    learners=api.get("/api/review/learners",headers=HEADERS).json()["learners"]
    sessions=api.get("/api/review/learners/learner-real/sessions",headers=HEADERS).json()["sessions"]
    detail=api.get(f"/api/review/learners/learner-real/sessions/{started['tutoring_session_id']}",headers=HEADERS)
    assert learners==[{"learner_id":"learner-real","pseudonym":"Sol","age_years":9}]
    assert [item["session_id"] for item in sessions]==[started["tutoring_session_id"]]
    assert detail.status_code==200 and detail.json()["learner"]["pseudonym"]=="Sol"


def wav_audio():
    target=BytesIO()
    with wave.open(target,"wb") as audio:
        audio.setnchannels(1); audio.setsampwidth(1); audio.setframerate(8000); audio.writeframes(b"\x80"*8000)
    return SelectedAudio(target.getvalue(),1)


def real_clip_client(tmp_path):
    database=tmp_path/"clips.db"; migrate(database); repository=SQLiteTutoringRepository(database)
    root=Path("src/math_tutor/curricula"); curriculum,_=load_curriculum_catalogs(root/"primary-math-v1.yaml",root/"activity-templates-v1.yaml")
    now=datetime.now(timezone.utc); store=OpaqueClipStore(tmp_path/"clips")
    retention=ClipRetentionService(repository,store,RetentionSettings(enabled=True,retention_days=2,evidence_directory=tmp_path/"clips"),now=lambda:now)
    service=ProvisioningService(repository,curriculum,retention)
    service.create_learner(CreateLearner("learner-clips","Nube",9))
    service.create_learning_plan(CreateLearningPlan("plan-clips","learner-clips",("units-tens",),(),SessionLimits(10,4)))
    consent=service.grant_audio_consent("learner-clips",retention_days=2,now=now)
    session=service.start_learning_session(StartLearningSession("learner-clips",consent.consent_id))
    def persist(suffix):
        activity_id=f"activity-{suffix}"; observation_id=f"observation-{suffix}"; evidence_id=f"evidence-{suffix}"
        activity=Activity("template-clip","units-tens",1,"¿Cuántas unidades?",{"number":4},StructuredAnswer.evaluable(ExpectedAnswerKind.INTEGER,{"answer":4}),(),())
        observation=Observation(observation_id,"learner-clips",session.tutoring_session_id,"units-tens",activity_id,ObservationOutcome.CORRECT,.95,0,TranscriptionReliabilityPolicy(.7),"cuatro")
        with repository._connect() as db:
            db.execute("INSERT INTO activities(session_id,activity_id,objective_id,activity_json) VALUES(?,?,?,?)",(session.tutoring_session_id,activity_id,"units-tens",_dump(activity)))
            db.execute("INSERT INTO observations(observation_id,session_id,learner_id,objective_id,activity_id,observation_json,version) VALUES(?,?,?,?,?,?,1)",(observation_id,session.tutoring_session_id,"learner-clips","units-tens",activity_id,_dump(observation)))
            db.execute("INSERT INTO evidence_records(evidence_id,observation_id,learner_id,session_id,objective_id,reason_for_retention) VALUES(?,?,?,?,?,?)",(evidence_id,observation_id,"learner-clips",session.tutoring_session_id,"units-tens","review-sample"))
        return retention.persist_selected(evidence_id=evidence_id,learner_id="learner-clips",session_id=session.tutoring_session_id,consent_snapshot_id=session.audio_consent_snapshot_id,selected=wav_audio(),evidence_selection_committed=True)
    clip_ids={kind:persist(kind) for kind in ("live","expired","revoked","deleting")}
    with repository._connect() as db:
        db.execute("UPDATE evidence_clips SET expires_at=? WHERE clip_id=?",((now-timedelta(seconds=1)).isoformat(),clip_ids["expired"]))
        db.execute("UPDATE evidence_clips SET deletion_state='deleting' WHERE clip_id=?",(clip_ids["deleting"],))
        # Revoke only one clip's authorisation by moving it to a separately revoked consent scope.
        revoked_id="revoked-consent"
        db.execute("INSERT INTO audio_consents(consent_id,learner_id,plan_id,plan_version,retention_days,granted_at,revoked_at,version) VALUES(?,?,?,?,?,?,?,?)",(revoked_id,"learner-clips","plan-clips",1,2,now.isoformat(),now.isoformat(),2))
        db.execute("UPDATE evidence_clips SET consent_id=? WHERE clip_id=?",(revoked_id,clip_ids["revoked"]))
    api=TestClient(create_app(WebSettings(True,TOKEN),provisioning=service))
    return api,repository,store,session.tutoring_session_id,clip_ids


def test_real_http_clip_access_denies_invalid_lifecycle_and_deletes_durably(tmp_path):
    api,repository,store,session_id,clips=real_clip_client(tmp_path)
    base=f"/api/review/learners/learner-clips/sessions/{session_id}/clips"
    live=api.get(f"{base}/{clips['live']}",headers=HEADERS)
    assert live.status_code==200 and live.content.startswith(b"RIFF")
    for kind in ("expired","revoked","deleting"):
        denied=api.get(f"{base}/{clips[kind]}",headers=HEADERS)
        assert denied.status_code==404 and str(store.root) not in denied.text
    assert api.get(f"/api/review/learners/other/sessions/{session_id}/clips/{clips['live']}",headers=HEADERS).status_code==404
    storage_key=repository.load_evidence_clip(clips["live"]).storage_key
    assert api.delete(f"{base}/{clips['live']}",headers=HEADERS).json()=={"status":"deleted"}
    assert not (store.root/storage_key).exists() and repository.load_evidence_clip(clips["live"]) is None
    with repository._connect() as db:
        assert db.execute("SELECT reason FROM audio_clip_deletion_tombstones WHERE clip_id=?",(clips["live"],)).fetchone()[0]=="explicit-delete"
    assert api.delete(f"{base}/{clips['live']}",headers=HEADERS).json()=={"status":"already-deleted"}
