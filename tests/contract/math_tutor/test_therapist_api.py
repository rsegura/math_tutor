from fastapi.testclient import TestClient
import pytest

from math_tutor.application.provisioning import ProvisioningService
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs
from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository
from web.app import WebSettings, create_app


class Purger:
    def purge_consent_scope(self, consent_id, session_ids): pass


def client(tmp_path, token="server-secret-value"):
    path = tmp_path / "api.db"; migrate(path)
    root = __import__("pathlib").Path("src/math_tutor/curricula")
    curriculum, _ = load_curriculum_catalogs(root / "primary-math-v1.yaml", root / "activity-templates-v1.yaml")
    service = ProvisioningService(SQLiteTutoringRepository(path), curriculum, Purger())
    return TestClient(create_app(WebSettings(True, token), provisioning=service))


def test_enabled_api_rejects_missing_or_placeholder_secret():
    for token in ("", "replace-me", "changeme"):
        with pytest.raises(ValueError, match="THERAPIST_API_TOKEN"):
            WebSettings(True, token)


def test_every_therapist_request_requires_exact_bearer(tmp_path):
    api = client(tmp_path)
    assert api.post("/api/therapist/learners", json={"learner_id":"l1","pseudonym":"Luna","age_years":8}).status_code == 401
    assert api.post("/api/therapist/learners", headers={"Authorization":"Bearer wrong"}, json={"learner_id":"l1","pseudonym":"Luna","age_years":8}).status_code == 401
    response = api.post("/api/therapist/learners", headers={"Authorization":"Bearer server-secret-value"}, json={"learner_id":"l1","pseudonym":"Luna","age_years":8})
    assert response.status_code == 201
    assert "server-secret-value" not in response.text


def test_full_authorised_api_flow_and_stale_update(tmp_path):
    api = client(tmp_path); h={"Authorization":"Bearer server-secret-value"}
    assert api.post("/api/therapist/learners", headers=h, json={"learner_id":"l1","pseudonym":"Luna","age_years":8}).status_code == 201
    created = api.post("/api/therapist/learners/l1/plans", headers=h, json={"plan_id":"p1","objective_ids":["units-tens"],"adaptations":["short-instructions"],"limits":{"duration_minutes":10,"max_activities":4}})
    assert created.status_code == 201
    stale = api.put("/api/therapist/learners/l1/plans/p1", headers=h, json={"expected_version":0,"objective_ids":["units-tens"],"adaptations":[],"limits":{"duration_minutes":10,"max_activities":4}})
    assert stale.status_code == 409
    consent = api.post("/api/therapist/learners/l1/audio-consents", headers=h, json={"retention_days":2}).json()
    started = api.post("/api/therapist/learners/l1/sessions", headers=h, json={"audio_consent_id":consent["consent_id"]})
    assert started.status_code == 201 and started.json()["join_code"]
    revoked = api.delete(f'/api/therapist/learners/l1/audio-consents/{consent["consent_id"]}', headers=h)
    assert revoked.status_code == 200 and revoked.json()["active"] is False
