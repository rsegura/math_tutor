"""Composition root for the local HTTP process."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from fastapi import FastAPI

from math_tutor.application.provisioning import ProvisioningService
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs
from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository
from web.therapist_api import create_therapist_router


_PLACEHOLDERS = {"replace-me", "changeme", "change-me", "placeholder", "secret", "token"}


@dataclass(frozen=True, slots=True)
class WebSettings:
    therapist_api_enabled: bool
    therapist_api_token: str | None

    def __post_init__(self) -> None:
        token = (self.therapist_api_token or "").strip()
        if self.therapist_api_enabled and (len(token) < 16 or token.lower() in _PLACEHOLDERS):
            raise ValueError("THERAPIST_API_TOKEN must be a non-placeholder server secret when therapist API is enabled")

    @classmethod
    def from_environment(cls) -> "WebSettings":
        enabled = os.getenv("THERAPIST_API_ENABLED", "false").strip().lower() == "true"
        return cls(enabled, os.getenv("THERAPIST_API_TOKEN"))


class NoClipPurger:
    """Task-10 audit-only adapter; Task 12 replaces physical deletion."""
    def purge_consent_scope(self, consent_id: str, session_ids: tuple[str, ...]) -> None:
        return None


def create_app(settings: WebSettings | None = None, *, provisioning: ProvisioningService | None = None) -> FastAPI:
    settings = settings or WebSettings.from_environment()
    result = FastAPI(title="Math Tutor Voice PoC")
    if settings.therapist_api_enabled:
        if provisioning is None:
            database = Path(os.getenv("DATABASE_PATH", "/app/data/math_tutor.db"))
            migrate(database)
            base = Path(__file__).resolve().parents[1] / "src/math_tutor/curricula"
            curriculum, _ = load_curriculum_catalogs(base / "primary-math-v1.yaml", base / "activity-templates-v1.yaml")
            provisioning = ProvisioningService(SQLiteTutoringRepository(database), curriculum, NoClipPurger())
        result.include_router(create_therapist_router(provisioning, settings.therapist_api_token or ""))
    return result


app = create_app()
