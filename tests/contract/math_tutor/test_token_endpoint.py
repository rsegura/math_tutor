import base64
import json
from pathlib import Path

from fastapi.testclient import TestClient

from math_tutor.application.provisioning import CreateLearner, CreateLearningPlan, ProvisioningService, SessionLimits, StartLearningSession
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs
from math_tutor.infrastructure.dispatch import AGENT_NAME, DispatchMetadata
from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository
from web.app import WebSettings, create_app


class Purger:
    def purge_consent_scope(self, consent_id, session_ids): pass


def provision(tmp_path):
    path = tmp_path / "voice.db"; migrate(path)
    curriculum, _ = load_curriculum_catalogs(Path("src/math_tutor/curricula/primary-math-v1.yaml"), Path("src/math_tutor/curricula/activity-templates-v1.yaml"))
    repository = SQLiteTutoringRepository(path)
    service = ProvisioningService(repository, curriculum, Purger())
    service.create_learner(CreateLearner("learner-opaque", "Luz", 8))
    service.create_learning_plan(CreateLearningPlan("plan-opaque", "learner-opaque", ("units-tens",), ("short-instructions",), SessionLimits(10, 4)))
    started = service.start_learning_session(StartLearningSession("learner-opaque"))
    env = {"LIVEKIT_API_KEY":"devkey", "LIVEKIT_API_SECRET":"devsecret-devsecret-devsecret-0000", "LIVEKIT_PUBLIC_URL":"ws://public.test"}
    app = create_app(WebSettings(False, None), provisioning=service)
    # Router captures os.environ by reference, so set process values for this contract.
    import os
    os.environ.update(env)
    return TestClient(app), started


def payload(token):
    value = token.split(".")[1]; value += "=" * (-len(value) % 4)
    return json.loads(base64.urlsafe_b64decode(value))


def test_token_requires_existing_active_session_and_exact_short_lived_join_code(tmp_path):
    client, started = provision(tmp_path)
    assert client.post("/api/token", json={"tutoring_session_id":started.tutoring_session_id,"join_code":"wrong"}).status_code == 401
    response = client.post("/api/token", json={"tutoring_session_id":started.tutoring_session_id,"join_code":started.join_code})
    assert response.status_code == 200
    data = response.json(); claim = payload(data["participant_token"])
    dispatch = claim["roomConfig"]["agents"][0]
    assert dispatch["agentName"] == AGENT_NAME
    parsed = DispatchMetadata.parse(dispatch["metadata"], data["room_name"])
    assert parsed.tutoring_session_id == started.tutoring_session_id
    assert data["server_url"] == "ws://public.test"
    assert response.headers["cache-control"] == "no-store"


def test_token_does_not_create_state_and_join_code_is_single_use(tmp_path):
    client, started = provision(tmp_path)
    missing = client.post("/api/token", json={"tutoring_session_id":"missing","join_code":"x"})
    assert missing.status_code == 401
    body = {"tutoring_session_id":started.tutoring_session_id,"join_code":started.join_code}
    assert client.post("/api/token", json=body).status_code == 200
    assert client.post("/api/token", json=body).status_code == 401

