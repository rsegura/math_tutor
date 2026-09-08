from fastapi.testclient import TestClient
import pytest

from math_tutor.application.provisioning import ProvisioningService
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs
from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository
from web.app import WebSettings, create_app


class Purger:
    def purge_consent_scope(self, consent_id, session_ids): pass


TOKEN = "server-secret-value-123456789"


def client(tmp_path, token=TOKEN):
    path = tmp_path / "api.db"; migrate(path)
    root = __import__("pathlib").Path("src/math_tutor/curricula")
    curriculum, _ = load_curriculum_catalogs(root / "primary-math-v1.yaml", root / "activity-templates-v1.yaml")
    service = ProvisioningService(SQLiteTutoringRepository(path), curriculum, Purger())
    return TestClient(create_app(WebSettings(True, token), provisioning=service))


def test_enabled_api_rejects_missing_or_placeholder_secret():
    example = next(line.split("=", 1)[1] for line in __import__('pathlib').Path('.env.example').read_text().splitlines() if line.startswith('THERAPIST_API_TOKEN='))
    for token in ("", "replace-me", "changeme", example, "weak-secret", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"):
        with pytest.raises(ValueError, match="THERAPIST_API_TOKEN"):
            WebSettings(True, token)


def test_every_therapist_request_requires_exact_bearer(tmp_path):
    api = client(tmp_path)
    assert api.post("/api/therapist/learners", json={"learner_id":"l1","pseudonym":"Luna","age_years":8}).status_code == 401
    assert api.post("/api/therapist/learners", headers={"Authorization":"Bearer wrong"}, json={"learner_id":"l1","pseudonym":"Luna","age_years":8}).status_code == 401
    response = api.post("/api/therapist/learners", headers={"Authorization":f"Bearer {TOKEN}"}, json={"learner_id":"l1","pseudonym":"Luna","age_years":8})
    assert response.status_code == 201
    assert TOKEN not in response.text


def test_full_authorised_api_flow_and_stale_update(tmp_path):
    api = client(tmp_path); h={"Authorization":f"Bearer {TOKEN}"}
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


def test_plan_api_accepts_optional_regulation_policy_and_returns_snapshot(tmp_path):
    api = client(tmp_path); h={"Authorization":f"Bearer {TOKEN}"}
    api.post("/api/therapist/learners", headers=h, json={"learner_id":"l1","pseudonym":"Luna","age_years":8})
    policy={
        "allowed_strategies":["repeat-instruction", "redirect-gently", "validate-emotion", "take-short-pause"],
        "max_consecutive_regulation_turns":3,
    }
    created=api.post("/api/therapist/learners/l1/plans",headers=h,json={"plan_id":"p1","objective_ids":["units-tens"],"adaptations":[],"limits":{"duration_minutes":10,"max_activities":3},"regulation":policy})
    assert created.status_code == 201
    assert created.json()["regulation"] == policy


@pytest.mark.parametrize("regulation", [
    {"allowed_strategies":[],"max_consecutive_regulation_turns":4},
    {"allowed_strategies":["repeat-instruction"],"max_consecutive_regulation_turns":4},
    {"allowed_strategies":["repeat-instruction","repeat-instruction","redirect-gently","validate-emotion","take-short-pause"],"max_consecutive_regulation_turns":4},
    {"allowed_strategies":["unknown","redirect-gently","validate-emotion","take-short-pause"],"max_consecutive_regulation_turns":4},
    {"allowed_strategies":["repeat-instruction","redirect-gently","validate-emotion","take-short-pause"],"max_consecutive_regulation_turns":13},
])
def test_plan_api_rejects_invalid_regulation_policy(regulation, tmp_path):
    api=client(tmp_path); h={"Authorization":f"Bearer {TOKEN}"}
    api.post("/api/therapist/learners",headers=h,json={"learner_id":"l1","pseudonym":"Luna","age_years":8})
    response=api.post("/api/therapist/learners/l1/plans",headers=h,json={"plan_id":"p1","objective_ids":["units-tens"],"adaptations":[],"limits":{"duration_minutes":10,"max_activities":3},"regulation":regulation})
    assert response.status_code == 422


def test_server_secret_never_leaks_to_responses_logs_static_or_child_services(tmp_path, caplog):
    api = client(tmp_path); headers={"Authorization":f"Bearer {TOKEN}"}
    response = api.post("/api/therapist/learners", headers=headers, json={"learner_id":"opaque","pseudonym":"Sol","age_years":9})
    assets = "".join(path.read_text(errors="ignore") for path in __import__('pathlib').Path('web/static').rglob('*') if path.is_file())
    compose = __import__('pathlib').Path('docker-compose.yml').read_text()
    tooling_block = compose.split('  tooling:',1)[1].split('  agent:',1)[0]
    agent_block = compose.split('  agent:',1)[1].split('  web:',1)[0]
    assert TOKEN not in response.text
    assert TOKEN not in caplog.text
    assert TOKEN not in assets
    assert TOKEN not in api.get('/openapi.json').text
    assert "THERAPIST_API_TOKEN" not in tooling_block
    assert "THERAPIST_API_TOKEN" not in agent_block


@pytest.mark.parametrize(("payload", "path"), [
    ({"learner_id":"l1","pseudonym":"Luna","age_years":True}, "/api/therapist/learners"),
    ({"learner_id":"l1","pseudonym":"Luna","age_years":"8"}, "/api/therapist/learners"),
    ({"learner_id":" l1","pseudonym":"Luna","age_years":8}, "/api/therapist/learners"),
    ({"learner_id":"l1","pseudonym":" ","age_years":8}, "/api/therapist/learners"),
])
def test_learner_boundary_rejects_coercion_and_whitespace_without_side_effects(tmp_path, payload, path):
    api = client(tmp_path); headers={"Authorization":f"Bearer {TOKEN}"}
    assert api.post(path, headers=headers, json=payload).status_code == 422
    valid={"learner_id":"l1","pseudonym":"Luna","age_years":8}
    assert api.post(path, headers=headers, json=valid).status_code == 201


@pytest.mark.parametrize("field,value", [
    ("expected_version", True), ("expected_version", -1),
    ("expected_version", "0"), ("plan_id", " plan"),
])
def test_plan_boundary_rejects_invalid_versions_and_ids_without_mutation(tmp_path, field, value):
    api = client(tmp_path); headers={"Authorization":f"Bearer {TOKEN}"}
    api.post("/api/therapist/learners",headers=headers,json={"learner_id":"l1","pseudonym":"Luna","age_years":8})
    body={"plan_id":"p1","expected_version":0,"objective_ids":["units-tens"],"adaptations":[],"limits":{"duration_minutes":10,"max_activities":3}}
    body[field]=value
    assert api.post("/api/therapist/learners/l1/plans",headers=headers,json=body).status_code == 422
    body[field] = 0 if field == "expected_version" else "p1"
    assert api.post("/api/therapist/learners/l1/plans",headers=headers,json=body).status_code == 201


@pytest.mark.parametrize("limits", [
    {"duration_minutes":True,"max_activities":3},
    {"duration_minutes":"10","max_activities":3},
    {"duration_minutes":10,"max_activities":False},
    {"duration_minutes":10,"max_activities":0},
])
def test_plan_boundary_rejects_coercive_or_out_of_range_limits(tmp_path, limits):
    api = client(tmp_path); headers={"Authorization":f"Bearer {TOKEN}"}
    api.post("/api/therapist/learners",headers=headers,json={"learner_id":"l1","pseudonym":"Luna","age_years":8})
    body={"plan_id":"p1","expected_version":0,"objective_ids":["units-tens"],"adaptations":[],"limits":limits}
    assert api.post("/api/therapist/learners/l1/plans",headers=headers,json=body).status_code == 422
    body["limits"]={"duration_minutes":10,"max_activities":3}
    assert api.post("/api/therapist/learners/l1/plans",headers=headers,json=body).status_code == 201


@pytest.mark.parametrize("retention", [True, "2", 0, 31])
def test_consent_boundary_rejects_coercive_or_out_of_range_retention(tmp_path, retention):
    api = client(tmp_path); headers={"Authorization":f"Bearer {TOKEN}"}
    api.post("/api/therapist/learners",headers=headers,json={"learner_id":"l1","pseudonym":"Luna","age_years":8})
    api.post("/api/therapist/learners/l1/plans",headers=headers,json={"plan_id":"p1","objective_ids":["units-tens"],"adaptations":[],"limits":{"duration_minutes":10,"max_activities":3}})
    assert api.post("/api/therapist/learners/l1/audio-consents",headers=headers,json={"retention_days":retention}).status_code == 422


def test_update_and_session_boundaries_reject_boolean_version_and_non_string_consent(tmp_path):
    api = client(tmp_path); headers={"Authorization":f"Bearer {TOKEN}"}
    api.post("/api/therapist/learners",headers=headers,json={"learner_id":"l1","pseudonym":"Luna","age_years":8})
    plan={"plan_id":"p1","objective_ids":["units-tens"],"adaptations":[],"limits":{"duration_minutes":10,"max_activities":3}}
    api.post("/api/therapist/learners/l1/plans",headers=headers,json=plan)
    update={"expected_version":True,"objective_ids":["units-tens"],"adaptations":[],"limits":plan["limits"]}
    assert api.put("/api/therapist/learners/l1/plans/p1",headers=headers,json=update).status_code == 422
    assert api.post("/api/therapist/learners/l1/sessions",headers=headers,json={"audio_consent_id":123}).status_code == 422
