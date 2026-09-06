"""Bounded, transient audio and an opaque on-disk clip store."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
import os
from pathlib import Path
import re
import secrets


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


@dataclass(frozen=True, slots=True)
class AudioFrame:
    payload: bytes
    duration_seconds: float
    captured_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.payload, bytes):
            raise TypeError("audio frame payload must be bytes")
        if not 0 < self.duration_seconds <= 30:
            raise ValueError("audio frame duration must be positive and bounded")
        if self.captured_at.tzinfo is None:
            raise ValueError("audio frame timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class SelectedAudio:
    payload: bytes
    duration_seconds: float


class SessionAudioRingBuffer:
    """A per-session memory-only rolling window; it never writes by itself."""

    def __init__(self, *, context_seconds: float = 20, hard_cap_seconds: float = 30) -> None:
        if not 0 < context_seconds <= hard_cap_seconds <= 30:
            raise ValueError("audio context must be positive and no greater than 30 seconds")
        self._context = context_seconds
        self._cap = hard_cap_seconds
        self._frames: deque[AudioFrame] = deque()
        self._duration = 0.0
        self._closed = False

    @property
    def duration_seconds(self) -> float:
        return self._duration

    def append(self, frame: AudioFrame) -> None:
        if self._closed:
            return
        self._frames.append(frame)
        self._duration += frame.duration_seconds
        while self._frames and self._duration > self._cap:
            self._duration -= self._frames.popleft().duration_seconds

    def select(self, selected_at: datetime) -> SelectedAudio:
        if selected_at.tzinfo is None:
            raise ValueError("selection timestamp must be timezone-aware")
        if self._closed:
            return SelectedAudio(b"", 0.0)
        lower = selected_at - timedelta(seconds=self._context)
        chosen = [frame for frame in self._frames if lower <= frame.captured_at <= selected_at]
        duration = sum(frame.duration_seconds for frame in chosen)
        while chosen and duration > self._cap:
            duration -= chosen.pop(0).duration_seconds
        return SelectedAudio(b"".join(frame.payload for frame in chosen), duration)

    def close(self) -> None:
        self._frames.clear()
        self._duration = 0.0
        self._closed = True


class SessionAudioBuffers:
    def __init__(self, *, context_seconds: float = 20, hard_cap_seconds: float = 30) -> None:
        self._context, self._cap = context_seconds, hard_cap_seconds
        self._sessions: dict[str, SessionAudioRingBuffer] = {}

    def append(self, session_id: str, frame: AudioFrame) -> None:
        self._sessions.setdefault(session_id, SessionAudioRingBuffer(context_seconds=self._context, hard_cap_seconds=self._cap)).append(frame)

    def select(self, session_id: str, at: datetime) -> SelectedAudio:
        value = self._sessions.get(session_id)
        return value.select(at) if value else SelectedAudio(b"", 0.0)

    def close_session(self, session_id: str) -> None:
        value = self._sessions.pop(session_id, None)
        if value:
            value.close()


class OpaqueClipStore:
    """Atomic storage confined to one directory and opaque identifiers."""

    def __init__(self, root: str | Path) -> None:
        requested = Path(root)
        if requested.is_symlink():
            raise ValueError("clip root must not be a symlink")
        self.root = requested.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    @staticmethod
    def storage_key(clip_id: str) -> str:
        if not _OPAQUE_ID.fullmatch(clip_id):
            raise ValueError("clip id must be opaque")
        return f"{clip_id}.bin"

    def _path(self, storage_key: str) -> Path:
        if Path(storage_key).name != storage_key or not storage_key.endswith(".bin"):
            raise ValueError("invalid clip storage key")
        clip_id = storage_key[:-4]
        self.storage_key(clip_id)
        path = self.root / storage_key
        if path.is_symlink():
            raise ValueError("clip path must not be a symlink")
        return path

    def write(self, clip_id: str, payload: bytes) -> str:
        key = self.storage_key(clip_id)
        target = self._path(key)
        temporary = self.root / f".{clip_id}.{secrets.token_hex(8)}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return key

    def delete(self, storage_key: str) -> None:
        path = self._path(storage_key)
        path.unlink(missing_ok=True)

    def read(self, storage_key: str) -> bytes:
        path = self._path(storage_key)
        return path.read_bytes()
