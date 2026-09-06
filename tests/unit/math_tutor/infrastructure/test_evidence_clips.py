from datetime import datetime, timedelta, timezone

import pytest

from math_tutor.infrastructure.evidence_clips import AudioFrame, OpaqueClipStore, SessionAudioRingBuffer


def test_ring_buffer_is_bounded_and_selection_uses_only_context_window():
    now = datetime(2026, 9, 6, tzinfo=timezone.utc)
    buffer = SessionAudioRingBuffer(context_seconds=3, hard_cap_seconds=5)
    for index in range(6):
        buffer.append(AudioFrame(bytes([index]), 1.0, now + timedelta(seconds=index)))

    selected = buffer.select(now + timedelta(seconds=6))

    assert selected.payload == bytes([3, 4, 5])
    assert selected.duration_seconds == 3


def test_session_close_discards_pending_audio():
    buffer = SessionAudioRingBuffer(context_seconds=3, hard_cap_seconds=5)
    buffer.append(AudioFrame(b"private", 1.0, datetime.now(timezone.utc)))
    buffer.close()
    assert buffer.select(datetime.now(timezone.utc)).payload == b""


def test_clip_store_rejects_path_traversal_and_symlinks(tmp_path):
    store = OpaqueClipStore(tmp_path / "clips")
    with pytest.raises(ValueError):
        store.write("../learner-transcript", b"private")
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        OpaqueClipStore(linked)


def test_clip_store_writes_atomically_with_opaque_names(tmp_path):
    store = OpaqueClipStore(tmp_path / "clips")
    key = store.write("opaque_abcdefghijklmnop", b"encoded")
    assert key == "opaque_abcdefghijklmnop.bin"
    assert store.read(key) == b"encoded"
    assert not list(store.root.glob("*.tmp"))
