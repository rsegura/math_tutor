from pathlib import Path
from fastapi.testclient import TestClient

from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository
from web.app import WebSettings, create_app


def test_review_endpoint_fails_closed_without_therapist_credential(tmp_path):
    path=tmp_path/"review.db"; migrate(path)
    app=create_app(WebSettings(False,None),database_path=path)
    response=TestClient(app).get("/api/review/sessions/missing")
    assert response.status_code == 401

