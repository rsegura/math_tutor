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
from math_tutor.application.review import TherapistReviewService
from math_tutor.domain.learning import AssistanceThreshold, CompetencyState, ProgressionPolicy
from web.therapist_api import create_therapist_router
from web.token_api import create_token_router
from web.review_api import RetainedClipAccess, create_review_router


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
    retention_service = None
    if settings.therapist_api_enabled:
        if provisioning is None:
            base = Path(__file__).resolve().parents[1] / "src/math_tutor/curricula"
            curriculum, _ = load_curriculum_catalogs(base / "primary-math-v1.yaml", base / "activity-templates-v1.yaml")
            retention = RetentionSettings.from_environment(os.environ)
            retention_service = ClipRetentionService(repository, OpaqueClipStore(retention.evidence_directory), retention)
            provisioning = ProvisioningService(repository, curriculum, retention_service)
        else:
            candidate = getattr(provisioning, "clip_purger", None)
            if isinstance(candidate, ClipRetentionService): retention_service = candidate
        result.include_router(create_therapist_router(provisioning, settings.therapist_api_token or ""))
    result.include_router(create_token_router(repository))
    thresholds = tuple(AssistanceThreshold(state, 3) for state in (
        CompetencyState.EXPLORING, CompetencyState.WITH_INTENSIVE_HELP,
        CompetencyState.WITH_LIGHT_HELP, CompetencyState.INDEPENDENT,
        CompetencyState.GENERALIZED,
    ))
    review_service = TherapistReviewService(repository, ProgressionPolicy(thresholds))
    clip_access = RetainedClipAccess(repository, retention_service) if retention_service else None
    result.include_router(create_review_router(repository, settings.therapist_api_token,
                                               review_service=review_service, clip_access=clip_access))

    @result.middleware("http")
    async def sensitive_response_headers(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/api/review", "/api/therapist", "/tutoring-review")):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            response.headers["Content-Security-Policy"] = "default-src 'self'; connect-src 'self'; media-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        return response
    result.mount("/", StaticFiles(directory=Path(__file__).with_name("static"), html=True), name="static")
    return result


app = create_app()
