"""Authenticated, evidence-linked therapist review boundary."""

from dataclasses import asdict, is_dataclass
import json
from typing import Annotated, Literal, Protocol

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from math_tutor.application.review import CorrectSkillEstimate, DiscardEvidence, ReviewStatus
from math_tutor.application.summary import SummaryService
from math_tutor.domain.learning import CompetencyState
from web.therapist_api import therapist_authorizer


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Command(StrictModel):
    command_id: str = Field(min_length=1, max_length=128)
    review_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=500)
    expected_review_version: int = Field(ge=0)
    expected_profile_version: int = Field(ge=1)

    @field_validator("command_id", "review_id", "reason")
    @classmethod
    def trimmed(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("must be trimmed and nonblank")
        return value


class DiscardBody(_Command):
    kind: Literal["discard-evidence"]
    evidence_id: str = Field(min_length=1, max_length=128)

    @field_validator("evidence_id")
    @classmethod
    def trimmed_evidence(cls, value: str) -> str:
        if value != value.strip(): raise ValueError("must be trimmed and nonblank")
        return value


class CorrectEstimateBody(_Command):
    kind: Literal["correct-skill-estimate"]
    objective_id: str = Field(min_length=1, max_length=128)
    corrected_state: CompetencyState

    @field_validator("objective_id")
    @classmethod
    def trimmed_objective(cls, value: str) -> str:
        if value != value.strip(): raise ValueError("must be trimmed and nonblank")
        return value

    @field_validator("corrected_state", mode="before")
    @classmethod
    def parse_state(cls, value):
        if not isinstance(value, str): raise ValueError("corrected_state must be a string")
        return CompetencyState(value)


CorrectionBody = Annotated[DiscardBody | CorrectEstimateBody, Field(discriminator="kind")]


class ClipAccess(Protocol):
    def load(self, clip_id: str): ...
    def read(self, record) -> bytes: ...
    def delete(self, clip_id: str, *, learner_id: str, session_id: str) -> bool | None: ...


class RetainedClipAccess:
    """Ownership-preserving adapter over Task 12's retention service and store."""
    def __init__(self, repository, retention) -> None:
        self.repository, self.retention = repository, retention

    def load(self, clip_id: str):
        return self.repository.load_evidence_clip(clip_id)

    def read(self, record) -> bytes:
        return self.retention.store.read(record.storage_key)

    def delete(self, clip_id: str, *, learner_id: str, session_id: str) -> bool | None:
        scope = self.repository.load_clip_review_scope(clip_id)
        if scope is None or scope[:2] != (learner_id, session_id): return None
        if not scope[2]: return False
        return bool(self.retention.delete_clip(clip_id, authorized=True))


def _safe_id(value: str) -> str:
    if not value or value != value.strip() or len(value) > 128:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not-found")
    return value


def _wire(value):
    if is_dataclass(value): value = asdict(value)
    if isinstance(value, dict): return {key: _wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)): return [_wire(item) for item in value]
    if hasattr(value, "value"): return value.value
    return value


def create_review_router(repository, therapist_token: str | None, *, summary_service=None,
                         review_service=None, clip_access: ClipAccess | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/review")
    summaries = summary_service or SummaryService(repository)

    auth = [Depends(therapist_authorizer(therapist_token))]

    def owned(learner_id: str, session_id: str):
        learner_id, session_id = _safe_id(learner_id), _safe_id(session_id)
        learner = repository.load_learner_profile(learner_id)
        aggregate = repository.load_session_aggregate(session_id)
        if learner is None or aggregate is None or aggregate.session.learner_id != learner_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "session-not-found")
        return learner, aggregate

    @router.get("/learners", dependencies=auth)
    def learners():
        return {"learners": [_wire(item) for item in repository.list_review_learners()]}

    @router.get("/learners/{learner_id}/sessions", dependencies=auth)
    def sessions(learner_id: str):
        learner_id = _safe_id(learner_id)
        if repository.load_learner_profile(learner_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "learner-not-found")
        return {"sessions": [_wire(item) for item in repository.list_review_sessions(learner_id)]}

    @router.get("/learners/{learner_id}/sessions/{session_id}", dependencies=auth)
    def detail(learner_id: str, session_id: str):
        learner, aggregate = owned(learner_id, session_id)
        summary = summaries.build(session_id)
        clip_by_evidence = {item.evidence_id: item for item in aggregate.clips}
        discarded = set(summary.discarded_evidence_ids)
        evidence = []
        for item in aggregate.evidence:
            if item.evidence_id in discarded: continue
            observation = item.observation
            clip = clip_by_evidence.get(item.evidence_id)
            evidence.append({"evidence_id":item.evidence_id,"objective_id":observation.objective_id,
                "outcome":observation.outcome.value,"assistance_level":observation.assistance_level,
                "stt_confidence":observation.stt_confidence,"response_excerpt":observation.transcribed_response[:240],
                "retention_reason":item.reason_for_retention,"interpretation":item.current_interpretation,
                "interpretation_status":"hypothesis" if item.current_interpretation else None,
                "clip":None if clip is None else {"clip_id":clip.clip_id,"duration_seconds":clip.duration_seconds,"expires_at":clip.expires_at}})
        return {"learner":{"learner_id":learner.learner_id,"pseudonym":learner.pseudonym,"age_years":learner.age_years},
            "session_id":session_id,"session_version":summary.source_session_version,"profile_version":summary.source_profile_version,
            "objectives":[{"objective_id":item.objective_id,"state":item.state.value,"estimate_version":item.version,"status":"provisional"} for item in aggregate.estimates],
            "summary":[{"claim_id":item.claim_id,"kind":item.kind,"text":item.text,"evidence_ids":list(item.evidence_ids),"status":"hypothesis" if item.is_hypothesis else "observation"} for item in summary.claims],
            "evidence":evidence,"history":[{"review_id":item.review_id,"version":item.version,"action":_wire(item.review)} for item in aggregate.reviews]}

    @router.get("/learners/{learner_id}/sessions/{session_id}/clips/{clip_id}", dependencies=auth)
    def clip(learner_id: str, session_id: str, clip_id: str):
        owned(learner_id, session_id)
        record = None if clip_access is None else clip_access.load(_safe_id(clip_id))
        if record is None or (record.learner_id, record.session_id) != (learner_id, session_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "clip-not-found")
        try: payload = clip_access.read(record)
        except (FileNotFoundError, ValueError): raise HTTPException(status.HTTP_404_NOT_FOUND, "clip-not-found") from None
        return Response(payload, media_type="audio/wav", headers={"Cache-Control":"no-store"})

    @router.delete("/learners/{learner_id}/sessions/{session_id}/clips/{clip_id}", dependencies=auth)
    def delete_clip(learner_id: str, session_id: str, clip_id: str):
        owned(learner_id, session_id)
        if clip_access is None: raise HTTPException(status.HTTP_404_NOT_FOUND, "clip-not-found")
        deleted = clip_access.delete(_safe_id(clip_id), learner_id=learner_id, session_id=session_id)
        if deleted is None: raise HTTPException(status.HTTP_404_NOT_FOUND, "clip-not-found")
        return {"status":"deleted" if deleted else "already-deleted"}

    @router.post("/learners/{learner_id}/sessions/{session_id}/corrections", dependencies=auth)
    def correct(learner_id: str, session_id: str, body: CorrectionBody):
        owned(learner_id, session_id)
        if review_service is None: raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "review-service-unavailable")
        common = dict(command_id=body.command_id, command_fingerprint="server-derived", review_id=body.review_id,
            learner_id=learner_id, session_id=session_id, reason=body.reason,
            expected_review_version=body.expected_review_version, expected_profile_version=body.expected_profile_version)
        result = (review_service.discard_evidence(DiscardEvidence(evidence_id=body.evidence_id, **common))
                  if isinstance(body, DiscardBody) else review_service.correct_skill_estimate(CorrectSkillEstimate(objective_id=body.objective_id, corrected_state=body.corrected_state, **common)))
        code = status.HTTP_409_CONFLICT if result.status in {ReviewStatus.CONFLICT,ReviewStatus.COLLISION} else status.HTTP_422_UNPROCESSABLE_ENTITY if result.status is ReviewStatus.REJECTED else status.HTTP_200_OK
        return Response(json.dumps(_wire(result)), status_code=code, media_type="application/json", headers={"Cache-Control":"no-store"})

    @router.get("/sessions/{session_id}", dependencies=auth)
    def session_state(session_id: str):
        state = repository.load_state(_safe_id(session_id))
        if state is None: raise HTTPException(status.HTTP_404_NOT_FOUND, "session-not-found")
        return {"session_id":state.session.session_id,"learner_id":state.session.learner_id,"plan_id":state.session.plan_id,"plan_version":state.session.plan_version,"active":state.session.can_continue,"objective_ids":state.session.authorised_objective_ids}

    return router
