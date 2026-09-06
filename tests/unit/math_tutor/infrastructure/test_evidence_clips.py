from datetime import datetime, timedelta, timezone
from io import BytesIO
import wave
import struct

import pytest

from math_tutor.infrastructure.evidence_clips import AudioFrame, OpaqueClipStore, SessionAudioBuffers, SessionAudioRingBuffer


def test_ring_buffer_is_bounded_and_selection_uses_only_context_window():
    now = datetime(2026, 9, 6, tzinfo=timezone.utc)
    buffer = SessionAudioRingBuffer(context_seconds=3, hard_cap_seconds=5, sample_rate=1, channels=1, sample_width=2)
    for index in range(6):
        buffer.append(AudioFrame(struct.pack("<h", index), 1.0, now + timedelta(seconds=index), sample_rate=1, channels=1, sample_width=2))

    selected = buffer.select(now + timedelta(seconds=6))

    with wave.open(BytesIO(selected.payload), "rb") as audio:
        assert audio.getparams()[:3] == (1, 2, 1)
        assert struct.unpack("<3h", audio.readframes(3)) == (3, 4, 5)
    assert selected.duration_seconds == 3


def test_session_close_discards_pending_audio():
    buffer = SessionAudioRingBuffer(context_seconds=3, hard_cap_seconds=5, sample_rate=1, channels=1, sample_width=2)
    buffer.append(AudioFrame(struct.pack("<h", 1), 1.0, datetime.now(timezone.utc), sample_rate=1, channels=1, sample_width=2))
    buffer.close()
    assert buffer.select(datetime.now(timezone.utc)).payload == b""


def test_long_overlapping_frame_is_trimmed_at_sample_boundary():
    now = datetime(2026, 9, 6, tzinfo=timezone.utc)
    buffer = SessionAudioRingBuffer(context_seconds=2, hard_cap_seconds=3, sample_rate=10, channels=1, sample_width=2)
    buffer.append(AudioFrame(struct.pack("<40h", *range(40)), 4.0, now, sample_rate=10, channels=1, sample_width=2))
    selected = buffer.select(now + timedelta(seconds=4))
    with wave.open(BytesIO(selected.payload), "rb") as audio:
        assert struct.unpack("<20h", audio.readframes(40)) == tuple(range(20, 40))
        assert audio.getnframes() / audio.getframerate() == 2


def test_buffer_rejects_inconsistent_audio_metadata():
    with pytest.raises(ValueError):
        AudioFrame(b"too-short", 1.0, datetime.now(timezone.utc), sample_rate=16000, channels=1, sample_width=2)


def test_selected_audio_rejects_non_wav_payload():
    from math_tutor.infrastructure.evidence_clips import SelectedAudio
    with pytest.raises(ValueError):
        SelectedAudio(b"raw-pcm-is-not-playable", 1)


def test_session_buffers_never_allocate_without_feature_and_exact_live_consent():
    now = datetime.now(timezone.utc)
    frame = AudioFrame(struct.pack("<h", 1), 1, now, 1, 1, 2)
    disabled = SessionAudioBuffers(enabled=False, authorize=lambda session_id, at: True, sample_rate=1, sample_width=2)
    disabled.append("no-consent", frame)
    assert disabled.buffered_duration("no-consent") == 0
    denied = SessionAudioBuffers(enabled=True, authorize=lambda session_id, at: False, sample_rate=1, sample_width=2)
    denied.append("wrong-snapshot", frame)
    assert denied.buffered_duration("wrong-snapshot") == 0


def test_consent_revocation_clears_and_permanently_disables_live_buffer():
    active = [True]
    now = datetime.now(timezone.utc)
    buffers = SessionAudioBuffers(enabled=True, authorize=lambda session_id, at: active[0], sample_rate=1, sample_width=2)
    buffers.append("session", AudioFrame(struct.pack("<h", 1), 1, now, 1, 1, 2))
    assert buffers.buffered_duration("session") == 1
    active[0] = False
    buffers.append("session", AudioFrame(struct.pack("<h", 2), 1, now + timedelta(seconds=1), 1, 1, 2))
    active[0] = True
    buffers.append("session", AudioFrame(struct.pack("<h", 3), 1, now + timedelta(seconds=2), 1, 1, 2))
    assert buffers.buffered_duration("session") == 0


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
    assert key == "opaque_abcdefghijklmnop.wav"
    assert store.read(key) == b"encoded"
    assert not list(store.root.glob("*.tmp"))


def test_quarantined_legacy_delete_is_confined_to_evidence_root(tmp_path):
    store = OpaqueClipStore(tmp_path / "clips")
    legacy = store.root / "old" / "clip.enc"
    legacy.parent.mkdir(); legacy.write_bytes(b"old")
    store.delete_quarantined_legacy("old/clip.enc")
    assert not legacy.exists()
    with pytest.raises(ValueError):
        store.delete_quarantined_legacy("../outside.enc")
