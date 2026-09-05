"""Authenticated FastAPI boundary for therapist provisioning commands."""

from __future__ import annotations

from dataclasses import asdict
import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from math_tutor.application.provisioning import (
    CreateLearner, CreateLearningPlan, ProvisioningError, ProvisioningService,
    RevokeAudioConsent, SessionLimits, StartLearningSession, UpdateLearningPlan,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class LearnerBody(StrictModel):
    learner_id: str = Field(min_length=1, max_length=128)
    pseudonym: str = Field(min_length=1, max_length=80)
    age_years: int = Field(ge=6, le=13)

    @field_validator("learner_id", "pseudonym")
    @classmethod
    def trimmed(cls, value: str) -> str:
        if value != value.strip(): raise ValueError("must be trimmed and nonblank")
        return value


class LimitsBody(StrictModel):
    duration_minutes: int = Field(ge=5, le=30)
    max_activities: int = Field(ge=1, le=20)


class PlanBody(StrictModel):
    plan_id: str = Field(min_length=1, max_length=128)
    objective_ids: list[str]
    adaptations: list[str]
    limits: LimitsBody
    expected_version: int = Field(default=0, ge=0)

    @field_validator("plan_id")
    @classmethod
    def trimmed_id(cls, value: str) -> str:
        if value != value.strip(): raise ValueError("must be trimmed and nonblank")
        return value

    @field_validator("objective_ids", "adaptations")
    @classmethod
    def trimmed_items(cls, values: list[str]) -> list[str]:
        if any(not value or value != value.strip() for value in values): raise ValueError("items must be trimmed and nonblank")
        return values


class PlanUpdateBody(StrictModel):
    expected_version: int = Field(ge=0)
    objective_ids: list[str]
    adaptations: list[str]
    limits: LimitsBody

    @field_validator("objective_ids", "adaptations")
    @classmethod
    def trimmed_items(cls, values: list[str]) -> list[str]:
        if any(not value or value != value.strip() for value in values): raise ValueError("items must be trimmed and nonblank")
        return values


class ConsentBody(StrictModel):
    retention_days: int = Field(ge=1, le=30)


class SessionBody(StrictModel):
    audio_consent_id: str | None = None


def _error(error: ProvisioningError) -> HTTPException:
    reason = str(error)
    if reason == "stale-plan-version" or reason.endswith("already-exists"):
        return HTTPException(status.HTTP_409_CONFLICT, reason)
    if reason.endswith("not-found"):
        return HTTPException(status.HTTP_404_NOT_FOUND, reason)
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, reason)


def create_therapist_router(service: ProvisioningService, token: str) -> APIRouter:
    router = APIRouter(prefix="/api/therapist")

    def authorised(authorization: str | None = Header(default=None)) -> None:
        expected = f"Bearer {token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unauthorised", headers={"WWW-Authenticate": "Bearer"})

    @router.post("/learners", status_code=status.HTTP_201_CREATED, dependencies=[Depends(authorised)])
    def create_learner(body: LearnerBody):
        try: return asdict(service.create_learner(CreateLearner(body.learner_id, body.pseudonym, body.age_years)))
        except ProvisioningError as error: raise _error(error) from error

    @router.post("/learners/{learner_id}/plans", status_code=status.HTTP_201_CREATED, dependencies=[Depends(authorised)])
    def create_plan(learner_id: str, body: PlanBody):
        try:
            value = service.create_learning_plan(CreateLearningPlan(body.plan_id, learner_id, tuple(body.objective_ids), tuple(body.adaptations), SessionLimits(body.limits.duration_minutes, body.limits.max_activities), body.expected_version))
            return {"plan_id": value.plan_id, "learner_id": learner_id, "version": value.version, "objective_ids": value.plan.authorised_objective_ids, "adaptations": value.adaptations, "limits": asdict(value.limits)}
        except ProvisioningError as error: raise _error(error) from error

    @router.put("/learners/{learner_id}/plans/{plan_id}", dependencies=[Depends(authorised)])
    def update_plan(learner_id: str, plan_id: str, body: PlanUpdateBody):
        try:
            value = service.update_learning_plan(UpdateLearningPlan(plan_id, learner_id, tuple(body.objective_ids), tuple(body.adaptations), SessionLimits(body.limits.duration_minutes, body.limits.max_activities), body.expected_version))
            return {"plan_id": value.plan_id, "learner_id": learner_id, "version": value.version}
        except ProvisioningError as error: raise _error(error) from error

    @router.post("/learners/{learner_id}/audio-consents", status_code=status.HTTP_201_CREATED, dependencies=[Depends(authorised)])
    def grant_consent(learner_id: str, body: ConsentBody):
        try:
            consent = service.grant_audio_consent(learner_id, retention_days=body.retention_days)
            return {"consent_id": consent.consent_id, "learner_id": consent.learner_id, "plan_id": consent.plan_id, "plan_version": consent.plan_version, "retention_days": consent.retention_days, "active": consent.active, "version": consent.version}
        except (ProvisioningError, ValueError) as error: raise _error(ProvisioningError(str(error))) from error

    @router.delete("/learners/{learner_id}/audio-consents/{consent_id}", dependencies=[Depends(authorised)])
    def revoke_consent(learner_id: str, consent_id: str):
        try:
            consent = service.revoke_audio_consent(RevokeAudioConsent(learner_id, consent_id))
            return {"consent_id": consent.consent_id, "active": consent.active, "version": consent.version}
        except ProvisioningError as error: raise _error(error) from error

    @router.post("/learners/{learner_id}/sessions", status_code=status.HTTP_201_CREATED, dependencies=[Depends(authorised)])
    def start_session(learner_id: str, body: SessionBody):
        try:
            started = service.start_learning_session(StartLearningSession(learner_id, body.audio_consent_id))
            return asdict(started)
        except ProvisioningError as error: raise _error(error) from error

    return router
