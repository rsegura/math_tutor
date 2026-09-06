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
            storage_key = self.store.storage_key(clip_id)
            expires_at = at + timedelta(days=days)
            if not self.repository.create_clip_write_intent(
                clip_id=clip_id, evidence_id=evidence_id, consent_id=consent_id,
                snapshot_id=consent_snapshot_id, duration_seconds=selected.duration_seconds,
                storage_key=storage_key, captured_at=at, expires_at=expires_at):
                return None
            try:
                self.store.write(clip_id, selected.payload)
                stored = self.repository.finalize_clip_write_intent(clip_id)
            except BaseException:
                self.store.delete(storage_key)
                self.repository.abandon_clip_write_intent(clip_id, reason="write-failed", deleted_at=self._now())
                raise
            if not stored:
                self.store.delete(storage_key)
                self.repository.abandon_clip_write_intent(clip_id, reason="authorization-lost", deleted_at=self._now())
                return None
            return clip_id

    def _purge(self, *, clip_id: str | None = None, consent_id: str | None = None, expired_before: datetime | None = None, reason: str) -> int:
        with self._mutation_lock:
            claimed = self.repository.claim_evidence_clips(clip_id=clip_id, consent_id=consent_id, expired_before=expired_before, claimed_at=self._now(), claim_id=self._claim_id, reason=reason)
            return self._delete_claimed(claimed, fallback_reason=reason)

    def _delete_claimed(self, claimed, *, fallback_reason: str) -> int:
        deleted = 0
        for record in claimed:
            try:
                self.store.delete(record.storage_key)
            except ValueError:
                if record.consent_id is not None:
                    raise
                self.store.delete_quarantined_legacy(record.storage_key)
            self.repository.complete_evidence_clip_deletion(record.clip_id, reason=record.deletion_reason or fallback_reason, deleted_at=self._now(), claim_id=self._claim_id)
            deleted += 1
        return deleted

    def sweep_expired(self) -> int:
        return self._purge(expired_before=self._now(), reason="expired")

    def maintenance(self) -> int:
        """Recover expired and stale-deleting rows, regardless of original expiry."""
        with self._mutation_lock:
            claimed = self.repository.claim_recoverable_evidence_clips(now=self._now(), claim_id=self._claim_id)
            return self._delete_claimed(claimed, fallback_reason="expired")

    def reconcile_startup(self) -> int:
        """Remove incomplete/unknown files before any clip becomes reviewable."""
        with self._mutation_lock:
            deleted = 0
            now = self._now()
            for intent in self.repository.claim_stale_clip_write_intents(now=now, claim_id=self._claim_id):
                try:
                    self.store.delete(intent.storage_key)
                except ValueError:
                    self.store.delete_quarantined_legacy(intent.storage_key)
                self.repository.abandon_clip_write_intent(intent.clip_id, reason="incomplete-write", deleted_at=self._now(), claim_id=self._claim_id)
                deleted += 1
            for storage_key in self.store.list_storage_keys():
                if not self.repository.claim_orphan_file(storage_key, now=self._now(), claim_id=self._claim_id):
                    continue
                try:
                    self.store.delete(storage_key)
                except ValueError:
                    self.store.delete_quarantined_legacy(storage_key)
                self.repository.complete_orphan_file_deletion(storage_key, deleted_at=self._now(), claim_id=self._claim_id)
                deleted += 1
            return deleted

    def delete_clip(self, clip_id: str, *, authorized: bool) -> int:
        if not authorized:
            raise PermissionError("clip deletion is not authorized")
        return self._purge(clip_id=clip_id, reason="explicit-delete")

    def purge_consent_scope(self, consent_id: str, session_ids: tuple[str, ...]) -> None:
        del session_ids  # scope ownership is derived from the durable consent relation
        self._purge(consent_id=consent_id, reason="consent-revoked")


class AsyncConsentGate:
    """Non-blocking cache refreshed off-loop; durable writes still revalidate."""
    def __init__(self, check: Callable[[], bool], *, initially_enabled: bool,
                 refresh_seconds: float = .25, on_revoked: Callable[[], None] | None = None) -> None:
        if not .01 <= refresh_seconds <= 5:
            raise ValueError("consent refresh must be between 0.01 and 5 seconds")
        self._check, self._candidate, self._active = check, initially_enabled, False
        self._refresh_seconds, self._on_revoked = refresh_seconds, on_revoked
        self._task: asyncio.Task | None = None

    def allows_capture(self) -> bool:
        return self._active

    async def refresh_once(self) -> None:
        if not self._candidate:
            return
        try:
            active = bool(await asyncio.to_thread(self._check))
        except Exception:
            active = False
        self._active = active
        if not active:
            self._candidate = False
            self._active = False
            if self._on_revoked is not None:
                self._on_revoked()

    async def _run(self) -> None:
        try:
            while self._candidate:
                await self.refresh_once()
                if self._candidate:
                    await asyncio.sleep(self._refresh_seconds)
        except asyncio.CancelledError:
            raise

    async def start(self) -> None:
        if self._candidate and self._task is None:
            self._task = asyncio.create_task(self._run())

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


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
