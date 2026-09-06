from pathlib import Path

from fastapi.testclient import TestClient

from web.app import WebSettings, create_app


def test_review_page_and_asset_are_no_store_and_contain_no_secret_or_diagnostic_labels(tmp_path):
    app=create_app(WebSettings(False,None),database_path=tmp_path/"review.db")
    api=TestClient(app)
    page=api.get("/tutoring-review.html"); script=api.get("/tutoring-review.js")
    assert page.status_code==script.status_code==200
    assert page.headers["cache-control"]==script.headers["cache-control"]=="no-store"
    combined=page.text+script.text
    assert "THERAPIST_API_TOKEN" not in combined
    assert "server-secret" not in combined
    assert "diagnóstico" not in combined.lower()
    assert "innerHTML" not in script.text


def test_review_page_exposes_business_review_sections_and_safe_csp(tmp_path):
    api=TestClient(create_app(WebSettings(False,None),database_path=tmp_path/"review.db"))
    response=api.get("/tutoring-review.html")
    for marker in ("Progreso por objetivo","Evidencias","Hipótesis","Correcciones","Historial"):
        assert marker in response.text
    assert "content-security-policy" in response.headers
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_static_review_client_uses_text_content_and_never_requests_full_transcript():
    script=Path("web/static/tutoring-review.js").read_text()
    assert ".textContent" in script
    assert "innerHTML" not in script
    assert "transcript" not in script.lower()
    assert "Authorization" in script
    assert "expected_profile_version" in script
    assert "correct-skill-estimate" in script
    assert "discard-evidence" in script
