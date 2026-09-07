from pathlib import Path
import subprocess


def _client_source() -> str:
    return Path("web/static/app.js").read_text()

def test_client_gets_token_before_connecting():
    source = _client_source()
    assert source.index("fetch(") < source.index("room.connect(")
    assert "tutoring_session_id" in source
    assert "join_code" in source


def test_client_registers_remote_audio_lifecycle_before_connecting():
    source = _client_source()

    assert source.index("RoomEvent.TrackSubscribed") < source.index("room.connect(")
    assert source.index("RoomEvent.TrackUnsubscribed") < source.index("room.connect(")
    assert source.index("RoomEvent.Disconnected") < source.index("room.connect(")
    assert "Track.Kind.Audio" in source
    assert "track.attach()" in source


def test_client_audio_lifecycle_executes_against_fake_livekit():
    completed = subprocess.run(
        ["node", "tests/contract/math_tutor/voice_client_contract.js"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_client_deduplicates_and_cleans_up_attached_audio_elements():
    source = _client_source()

    assert "attachedAudio.has(track.sid)" in source
    assert "attachedAudio.set(track.sid" in source
    assert "track.detach(element)" in source
    assert "element.remove()" in source
    assert "attachedAudio.clear()" in source


def test_client_starts_audio_from_join_gesture_and_keeps_room_alive():
    source = _client_source()

    submit_handler = source.index('form.addEventListener("submit"')
    first_await = source.index("await ", submit_handler)
    start_audio = source.index("room.startAudio()", submit_handler)

    assert start_audio < first_await
    assert "let activeRoom" in source
    assert "activeRoom = room" in source
    assert "navegador bloqueó el audio" in source


def test_client_has_a_managed_remote_audio_container():
    page = Path("web/static/index.html").read_text()

    assert 'id="remote-audio"' in page
    assert 'aria-hidden="true"' in page
