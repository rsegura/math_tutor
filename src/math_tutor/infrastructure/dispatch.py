"""Strict, privacy-minimal dispatch contract for a provisioned tutoring session."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json

from math_tutor.application.provisioning import LearnerProfile, ProvisionedPlan
from math_tutor.domain.learning import LearningSession


AGENT_NAME = "math-tutor-agent"
ROOM_PREFIX = "math-tutor-"


class DispatchMetadataError(ValueError):
    pass


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise DispatchMetadataError(f"{field} must be a nonempty opaque id")
    return value


def _version(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DispatchMetadataError(f"{field} must be a positive integer")
    return value


@dataclass(frozen=True, slots=True)
class DispatchMetadata:
    tutoring_session_id: str
    expected_session_version: int
    plan_id: str
    expected_plan_version: int

    def __post_init__(self) -> None:
        _identifier(self.tutoring_session_id, "tutoring_session_id")
        _version(self.expected_session_version, "expected_session_version")
        _identifier(self.plan_id, "plan_id")
        _version(self.expected_plan_version, "expected_plan_version")

    def as_dict(self) -> dict[str, object]:
        return {
            "tutoring_session_id": self.tutoring_session_id,
            "expected_session_version": self.expected_session_version,
            "plan_id": self.plan_id,
            "expected_plan_version": self.expected_plan_version,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), separators=(",", ":"), sort_keys=True)

    @classmethod
    def parse(cls, raw: str | None, room_name: str) -> "DispatchMetadata":
        try:
            value = json.loads(raw or "")
        except (json.JSONDecodeError, TypeError) as error:
            raise DispatchMetadataError("dispatch metadata must be valid JSON") from error
        expected = {"tutoring_session_id", "expected_session_version", "plan_id", "expected_plan_version"}
        if not isinstance(value, dict) or set(value) != expected:
            raise DispatchMetadataError("dispatch metadata has an invalid shape")
        result = cls(**value)
        if room_name != room_name_for(result.tutoring_session_id):
            raise DispatchMetadataError("room does not match tutoring session")
        return result


def room_name_for(session_id: str) -> str:
    return f"{ROOM_PREFIX}{_identifier(session_id, 'tutoring_session_id')}"


@dataclass(frozen=True, slots=True)
class VoiceBootstrap:
    learner: LearnerProfile
    plan: ProvisionedPlan
    session: LearningSession
    profile_version: int
    audio_consent_snapshot_id: str | None
    clip_capture_enabled: bool
    session_started_at: datetime


class VoiceBootstrapError(ValueError):
    pass


def verify_join_code(stored_hash: str, supplied_code: str) -> bool:
    if not isinstance(supplied_code, str) or not supplied_code:
        return False
    return __import__("hmac").compare_digest(
        stored_hash, hashlib.sha256(supplied_code.encode()).hexdigest()
    )
