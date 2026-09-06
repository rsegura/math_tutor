"""Composition root for the local HTTP process."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from math_tutor.application.provisioning import ProvisioningService
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs
from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository
from math_tutor.infrastructure.clip_retention import ClipRetentionService, RetentionSettings
from math_tutor.infrastructure.evidence_clips import OpaqueClipStore
from web.therapist_api import create_therapist_router
from web.token_api import create_token_router
from web.review_api import create_review_router


_PLACEHOLDERS = {"replace-me", "changeme", "change-me", "placeholder", "secret", "token", "weak-secret"}


@dataclass(frozen=True, slots=True)
class WebSettings:
    therapist_api_enabled: bool
    therapist_api_token: str | None

    def __post_init__(self) -> None:
        token = (self.therapist_api_token or "").strip()
        placeholder = token.lower() in _PLACEHOLDERS or any(marker in token.lower() for marker in ("replace-with", "placeholder", "example-token"))
        weak = len(token) < 24 or len(set(token)) < 8
        if self.therapist_api_enabled and (placeholder or weak):
            raise ValueError("THERAPIST_API_TOKEN must be a non-placeholder server secret when therapist API is enabled")

    @classmethod
    def from_environment(cls) -> "WebSettings":
        enabled = os.getenv("THERAPIST_API_ENABLED", "false").strip().lower() == "true"
        return cls(enabled, os.getenv("THERAPIST_API_TOKEN"))


def create_app(settings: WebSettings | None = None, *, provisioning: ProvisioningService | None = None, database_path: Path | None = None) -> FastAPI:
    settings = settings or WebSettings.from_environment()
    result = FastAPI(title="Math Tutor Voice PoC")
    repository = provisioning.repository if provisioning is not None else None
    if repository is None:
        database = database_path or Path(os.getenv("DATABASE_PATH", "/app/data/math_tutor.db"))
        migrate(database)
        repository = SQLiteTutoringRepository(database)
    if settings.therapist_api_enabled:
        if provisioning is None:
            base = Path(__file__).resolve().parents[1] / "src/math_tutor/curricula"
            curriculum, _ = load_curriculum_catalogs(base / "primary-math-v1.yaml", base / "activity-templates-v1.yaml")
            retention = RetentionSettings.from_environment(os.environ)
            clip_purger = ClipRetentionService(repository, OpaqueClipStore(retention.evidence_directory), retention)
            provisioning = ProvisioningService(repository, curriculum, clip_purger)
        result.include_router(create_therapist_router(provisioning, settings.therapist_api_token or ""))
    result.include_router(create_token_router(repository))
    result.include_router(create_review_router(repository, settings.therapist_api_token))
    result.mount("/", StaticFiles(directory=Path(__file__).with_name("static"), html=True), name="static")
    return result


app = create_app()
