from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from math_tutor.application.review import ReviewResult, ReviewStatus
from math_tutor.application.summary import SessionSummary, SummaryClaim
from math_tutor.domain.learning import CompetencyState, SkillEstimate
from math_tutor.infrastructure.persistence.repositories import EvidenceClipRecord
from web.review_api import create_review_router
from math_tutor.application.provisioning import ProvisioningService
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs
from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository
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


def client():
    app=FastAPI(); app.include_router(create_review_router(Queries(), TOKEN, summary_service=Summary(), review_service=Reviews(), clip_access=Clips()))
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
    assert all(response.headers["cache-control"]=="no-store" for response in (learners,sessions,detail))
    assert "diagnos" not in detail.text.lower()


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
