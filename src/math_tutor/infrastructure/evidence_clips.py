"""Bounded, transient audio and an opaque on-disk clip store."""

from __future__ import annotations

from collections import deque
from array import array
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import BytesIO
import os
from pathlib import Path
import re
import secrets
import sys
from typing import Callable
import wave


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


@dataclass(frozen=True, slots=True)
class AudioFrame:
    payload: bytes
    duration_seconds: float
    captured_at: datetime
    sample_rate: int
    channels: int
    sample_width: int

    def __post_init__(self) -> None:
        if not isinstance(self.payload, bytes):
            raise TypeError("audio frame payload must be bytes")
        if not 0 < self.duration_seconds <= 30:
            raise ValueError("audio frame duration must be positive and bounded")
        if self.captured_at.tzinfo is None:
            raise ValueError("audio frame timestamp must be timezone-aware")
        if isinstance(self.sample_rate, bool) or not isinstance(self.sample_rate, int) or self.sample_rate <= 0:
            raise ValueError("sample rate must be positive")
        if self.channels not in (1, 2) or self.sample_width != 2:
            raise ValueError("unsupported PCM layout")
        expected = self.duration_seconds * self.sample_rate * self.channels * self.sample_width
        if abs(len(self.payload) - expected) > self.channels * self.sample_width:
            raise ValueError("audio duration does not match PCM metadata")


@dataclass(frozen=True, slots=True)
class SelectedAudio:
    payload: bytes
    duration_seconds: float
    codec: str = "wav-pcm"

    def __post_init__(self) -> None:
        if not self.payload and self.duration_seconds == 0:
            return
        if self.codec != "wav-pcm" or not self.payload or self.duration_seconds <= 0:
            raise ValueError("selected audio must be playable WAV PCM")
        try:
            with wave.open(BytesIO(self.payload), "rb") as audio:
                actual = audio.getnframes() / audio.getframerate()
                if audio.getcomptype() != "NONE" or abs(actual - self.duration_seconds) > 1 / audio.getframerate():
                    raise ValueError("selected WAV metadata is inconsistent")
        except (EOFError, wave.Error, ZeroDivisionError) as error:
            raise ValueError("selected audio must be playable WAV PCM") from error


class SessionAudioRingBuffer:
    """A per-session memory-only rolling window; it never writes by itself."""

    def __init__(self, *, context_seconds: float = 20, hard_cap_seconds: float = 30,
                 sample_rate: int = 16_000, channels: int = 1, sample_width: int = 2) -> None:
        if not 0 < context_seconds <= hard_cap_seconds <= 30:
            raise ValueError("audio context must be positive and no greater than 30 seconds")
        self._context = context_seconds
        self._cap = hard_cap_seconds
        if sample_rate <= 0 or channels not in (1, 2) or sample_width != 2:
            raise ValueError("unsupported canonical PCM layout")
        self._rate, self._channels, self._width = sample_rate, channels, sample_width
        self._frames: deque[AudioFrame] = deque()
        self._duration = 0.0
        self._closed = False

    @property
    def duration_seconds(self) -> float:
        return self._duration

    def append(self, frame: AudioFrame) -> None:
        if self._closed:
            return
        payload = _normalize_pcm16(frame.payload, frame.sample_rate, frame.channels, self._rate, self._channels)
        duration = len(payload) / (self._rate * self._channels * self._width)
        normalized = AudioFrame(payload, duration, frame.captured_at, self._rate, self._channels, self._width)
        if duration > self._cap:
            normalized = self._trim_start(normalized, duration - self._cap)
            duration = normalized.duration_seconds
        self._frames.append(normalized)
        self._duration += duration
        while self._frames and self._duration > self._cap:
            excess = self._duration - self._cap
            oldest = self._frames[0]
            if oldest.duration_seconds <= excess + 1 / self._rate:
                self._duration -= self._frames.popleft().duration_seconds
            else:
                self._frames[0] = self._trim_start(oldest, excess)
                self._duration = self._cap

    def _trim_start(self, frame: AudioFrame, seconds: float) -> AudioFrame:
        sample_frames = min(round(seconds * self._rate), round(frame.duration_seconds * self._rate))
        offset = sample_frames * self._channels * self._width
        payload = frame.payload[offset:]
        duration = len(payload) / (self._rate * self._channels * self._width)
        return AudioFrame(payload, duration, frame.captured_at + timedelta(seconds=sample_frames / self._rate), self._rate, self._channels, self._width)

    def select(self, selected_at: datetime) -> SelectedAudio:
        if selected_at.tzinfo is None:
            raise ValueError("selection timestamp must be timezone-aware")
        if self._closed:
            return SelectedAudio(b"", 0.0)
        lower = selected_at - timedelta(seconds=self._context)
        chunks = []
        bytes_per_frame = self._channels * self._width
        for frame in self._frames:
            frame_end = frame.captured_at + timedelta(seconds=frame.duration_seconds)
            overlap_start, overlap_end = max(lower, frame.captured_at), min(selected_at, frame_end)
            if overlap_end <= overlap_start:
                continue
            first = round((overlap_start - frame.captured_at).total_seconds() * self._rate)
            last = round((overlap_end - frame.captured_at).total_seconds() * self._rate)
            chunks.append(frame.payload[first * bytes_per_frame:last * bytes_per_frame])
        pcm = b"".join(chunks)
        max_samples = round(min(self._context, self._cap) * self._rate)
        pcm = pcm[-max_samples * bytes_per_frame:]
        if not pcm:
            return SelectedAudio(b"", 0.0)
        encoded = BytesIO()
        with wave.open(encoded, "wb") as output:
            output.setnchannels(self._channels)
            output.setsampwidth(self._width)
            output.setframerate(self._rate)
            output.writeframes(pcm)
        return SelectedAudio(encoded.getvalue(), len(pcm) / (self._rate * bytes_per_frame))

    def close(self) -> None:
        self._frames.clear()
        self._duration = 0.0
        self._closed = True


class SessionAudioBuffers:
    def __init__(self, *, context_seconds: float = 20, hard_cap_seconds: float = 30,
                 sample_rate: int = 16_000, channels: int = 1, sample_width: int = 2,
                 enabled: bool = True, authorize: Callable[[str, datetime], bool] | None = None) -> None:
        self._context, self._cap = context_seconds, hard_cap_seconds
        self._format = sample_rate, channels, sample_width
        self._enabled = enabled
        self._authorize = authorize or (lambda session_id, at: True)
        self._sessions: dict[str, SessionAudioRingBuffer] = {}
        self._disabled_sessions: set[str] = set()

    def append(self, session_id: str, frame: AudioFrame) -> None:
        if not self._allowed(session_id, frame.captured_at):
            return
        self._sessions.setdefault(session_id, SessionAudioRingBuffer(context_seconds=self._context, hard_cap_seconds=self._cap, sample_rate=self._format[0], channels=self._format[1], sample_width=self._format[2])).append(frame)

    def select(self, session_id: str, at: datetime) -> SelectedAudio:
        if not self._allowed(session_id, at):
            return SelectedAudio(b"", 0.0)
        value = self._sessions.get(session_id)
        return value.select(at) if value else SelectedAudio(b"", 0.0)

    def _allowed(self, session_id: str, at: datetime) -> bool:
        if not self._enabled or session_id in self._disabled_sessions:
            return False
        if not self._authorize(session_id, at):
            self.disable_session(session_id)
            return False
        return True

    def disable_session(self, session_id: str) -> None:
        self.close_session(session_id)
        self._disabled_sessions.add(session_id)

    def buffered_duration(self, session_id: str) -> float:
        value = self._sessions.get(session_id)
        return value.duration_seconds if value else 0.0

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
        return f"{clip_id}.wav"

    def _path(self, storage_key: str) -> Path:
        if Path(storage_key).name != storage_key or not storage_key.endswith(".wav"):
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

    def delete_quarantined_legacy(self, storage_key: str) -> None:
        """Delete a pre-consent key only when it still resolves beneath root."""
        relative = Path(storage_key)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid quarantined clip storage key")
        candidate = self.root / relative
        for parent in (candidate, *candidate.parents):
            if parent == self.root:
                break
            if parent.is_symlink():
                raise ValueError("quarantined clip path must not cross a symlink")
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self.root):
            raise ValueError("quarantined clip path escapes evidence directory")
        resolved.unlink(missing_ok=True)

    def read(self, storage_key: str) -> bytes:
        path = self._path(storage_key)
        return path.read_bytes()


def _normalize_pcm16(payload: bytes, source_rate: int, source_channels: int,
                     target_rate: int, target_channels: int) -> bytes:
    """Deterministic PCM16 channel conversion and nearest-neighbour resampling."""
    samples = array("h")
    samples.frombytes(payload)
    if sys.byteorder != "little":
        samples.byteswap()
    frames = [tuple(samples[index:index + source_channels]) for index in range(0, len(samples), source_channels)]
    if source_channels != target_channels:
        if target_channels == 1:
            frames = [(round((frame[0] + frame[1]) / 2),) for frame in frames]
        else:
            frames = [(frame[0], frame[0]) for frame in frames]
    if source_rate != target_rate and frames:
        count = max(1, round(len(frames) * target_rate / source_rate))
        frames = [frames[min(len(frames) - 1, index * source_rate // target_rate)] for index in range(count)]
    output = array("h", (sample for frame in frames for sample in frame))
    if sys.byteorder != "little":
        output.byteswap()
    return output.tobytes()
