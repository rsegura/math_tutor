from fastapi.testclient import TestClient
from pathlib import Path

from web.app import WebSettings, create_app


def test_web_app_exposes_static_token_and_review_routes(tmp_path):
    app = create_app(WebSettings(False, None), database_path=tmp_path / "db.sqlite")
    paths = {route.path for route in app.routes}
    assert "/api/token" in paths
    assert "/api/review/sessions/{session_id}" in paths
    assert client_get(app, "/").status_code == 200


def client_get(app, path):
    with TestClient(app) as client:
        return client.get(path)


def test_compose_preserves_uvicorn_web_entrypoint():
    assert "uvicorn web.app:app" in Path("docker-compose.yml").read_text()
