from pathlib import Path

def test_client_gets_token_before_connecting():
    source = Path("web/static/app.js").read_text()
    assert source.index("fetch(") < source.index("room.connect(")
    assert "tutoring_session_id" in source
    assert "join_code" in source
