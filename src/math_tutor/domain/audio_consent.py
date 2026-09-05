"""Explicit, versioned permission for selective evidence audio."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AudioConsent:
    consent_id: str
    learner_id: str
    plan_id: str
    plan_version: int
    retention_days: int
    granted_at: datetime
    revoked_at: datetime | None = None
    version: int = 1

    def __post_init__(self) -> None:
        for value, label in ((self.consent_id, "consent id"), (self.learner_id, "learner id"), (self.plan_id, "plan id")):
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"{label} must be a trimmed nonempty string")
        if isinstance(self.plan_version, bool) or not isinstance(self.plan_version, int) or self.plan_version < 1:
            raise ValueError("plan version must be positive")
        if isinstance(self.retention_days, bool) or not 1 <= self.retention_days <= 30:
            raise ValueError("retention days must be between 1 and 30")
        if self.granted_at.tzinfo is None or (self.revoked_at is not None and self.revoked_at.tzinfo is None):
            raise ValueError("consent timestamps must be timezone-aware")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("consent version must be positive")

    @property
    def active(self) -> bool:
        return self.revoked_at is None

    def revoke(self, at: datetime) -> "AudioConsent":
        if not self.active:
            return self
        return replace(self, revoked_at=at, version=self.version + 1)


@dataclass(frozen=True, slots=True)
class SessionAudioConsent:
    snapshot_id: str
    consent_id: str
    learner_id: str
    session_id: str
    plan_id: str
    plan_version: int
    retention_days: int
    granted_at: datetime
