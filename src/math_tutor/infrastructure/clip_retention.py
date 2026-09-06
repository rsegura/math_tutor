"""Consent-gated selective clip persistence and retry-safe retention."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import secrets
from threading import RLock
from typing import Awaitable, Callable, Mapping

from math_tutor.infrastructure.evidence_clips import OpaqueClipStore, SelectedAudio


@dataclass(frozen=True, slots=True)
class RetentionSettings:
    enabled: bool = False
    retention_days: int = 7
    context_seconds: float = 20
    hard_cap_seconds: float = 30
    sweep_interval_seconds: int = 300
    evidence_directory: Path = Path("/app/data/evidence-clips")

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("audio evidence enabled must be boolean")
        if isinstance(self.retention_days, bool) or not isinstance(self.retention_days, int) or not 1 <= self.retention_days <= 30:
            raise ValueError("audio retention days must be between 1 and 30")
        if isinstance(self.sweep_interval_seconds, bool) or not isinstance(self.sweep_interval_seconds, int) or not 60 <= self.sweep_interval_seconds <= 3600:
            raise ValueError("audio sweep interval must be between 60 and 3600 seconds")
        if not 0 < self.context_seconds <= self.hard_cap_seconds <= 30:
            raise ValueError("audio clip window must be positive and at most 30 seconds")
        object.__setattr__(self, "evidence_directory", Path(self.evidence_directory))

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> "RetentionSettings":
        raw = env.get("AUDIO_EVIDENCE_ENABLED", "false").strip().lower()
        if raw not in {"true", "false"}:
            raise ValueError("AUDIO_EVIDENCE_ENABLED must be true or false")
        try:
            days = int(env.get("AUDIO_EVIDENCE_RETENTION_DAYS", "7"))
            context = float(env.get("AUDIO_EVIDENCE_CONTEXT_SECONDS", "20"))
            cap = float(env.get("AUDIO_EVIDENCE_HARD_CAP_SECONDS", "30"))
            interval = int(env.get("AUDIO_RETENTION_SWEEP_INTERVAL_SECONDS", "300"))
        except (TypeError, ValueError):
            raise ValueError("audio retention settings must be numeric") from None
        if not 1 <= days <= 30:
            raise ValueError("audio retention days must be between 1 and 30")
        if not 0 < context <= cap <= 30:
            raise ValueError("audio clip window must be positive and at most 30 seconds")
        if not 60 <= interval <= 3600:
            raise ValueError("audio sweep interval must be between 60 and 3600 seconds")
        return cls(raw == "true", days, context, cap, interval, Path(env.get("AUDIO_EVIDENCE_DIRECTORY", "/app/data/evidence-clips")))


class ClipRetentionService:
    def __init__(self, repository, store: OpaqueClipStore, settings: RetentionSettings, *, now=None) -> None:
        self.repository, self.store, self.settings = repository, store, settings
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._mutation_lock = RLock()
        self._claim_id = secrets.token_urlsafe(18)

    def persist_selected(self, *, evidence_id: str, learner_id: str, session_id: str,
                         consent_snapshot_id: str | None, selected: SelectedAudio,
                         evidence_selection_committed: bool) -> str | None:
        if not self.settings.enabled or not evidence_selection_committed or consent_snapshot_id is None or not selected.payload:
            return None
        if not 0 < selected.duration_seconds <= self.settings.hard_cap_seconds:
            raise ValueError("selected audio exceeds hard cap")
        with self._mutation_lock:
            at = self._now()
            authorization = self.repository.authorize_clip_capture(
                evidence_id=evidence_id, learner_id=learner_id, session_id=session_id,
                snapshot_id=consent_snapshot_id, captured_at=at,
            )
            if authorization is None:
                return None
            consent_id, consent_days = authorization
            days = min(self.settings.retention_days, consent_days, 30)
            clip_id = secrets.token_urlsafe(24)
            storage_key = self.store.write(clip_id, selected.payload)
            try:
                stored = self.repository.save_authorized_evidence_clip(
                    clip_id=clip_id, evidence_id=evidence_id, consent_id=consent_id,
                    snapshot_id=consent_snapshot_id, duration_seconds=selected.duration_seconds,
                    storage_key=storage_key, captured_at=at,
                    expires_at=at + timedelta(days=days),
                )
            except BaseException:
                self.store.delete(storage_key)
                raise
            if not stored:
                self.store.delete(storage_key)
                return None
            return clip_id

    def _purge(self, *, clip_id: str | None = None, consent_id: str | None = None, expired_before: datetime | None = None, reason: str) -> int:
        with self._mutation_lock:
            claimed = self.repository.claim_evidence_clips(clip_id=clip_id, consent_id=consent_id, expired_before=expired_before, claimed_at=self._now(), claim_id=self._claim_id)
            deleted = 0
            for record in claimed:
                self.store.delete(record.storage_key)
                self.repository.complete_evidence_clip_deletion(record.clip_id, reason=reason, deleted_at=self._now())
                deleted += 1
            return deleted

    def sweep_expired(self) -> int:
        return self._purge(expired_before=self._now(), reason="expired")

    def delete_clip(self, clip_id: str, *, authorized: bool) -> int:
        if not authorized:
            raise PermissionError("clip deletion is not authorized")
        return self._purge(clip_id=clip_id, reason="explicit-delete")

    def purge_consent_scope(self, consent_id: str, session_ids: tuple[str, ...]) -> None:
        del session_ids  # scope ownership is derived from the durable consent relation
        self._purge(consent_id=consent_id, reason="consent-revoked")


class RetentionSweeper:
    def __init__(self, sweep: Callable[[], object | Awaitable[object]], *, interval_seconds: int = 300) -> None:
        if not 60 <= interval_seconds <= 3600:
            raise ValueError("sweep interval must be between 60 and 3600 seconds")
        self._sweep, self._interval = sweep, interval_seconds
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    async def sweep_once(self) -> None:
        async with self._lock:
            if asyncio.iscoroutinefunction(self._sweep):
                await self._sweep()
            else:
                await asyncio.to_thread(self._sweep)

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._interval)
                await self.sweep_once()
        except asyncio.CancelledError:
            raise

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
