"""Authenticated, evidence-linked therapist review boundary."""

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
from typing import Annotated, Literal, Protocol

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from math_tutor.application.review import CorrectSkillEstimate, DiscardEvidence, ReviewStatus
from math_tutor.application.summary import SummaryService
from math_tutor.domain.learning import CompetencyState
from math_tutor.application.next_objectives import NextObjectiveDecision, NextObjectiveError, ProposalDecisionStatus
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


class NextObjectiveDecisionBody(StrictModel):
    command_id: str = Field(min_length=1,max_length=128)
    decision: Literal["approved","rejected"]
    reason: str = Field(min_length=1,max_length=500)
    expected_revision: int = Field(ge=0)
    expected_plan_version: int = Field(ge=1)
    expected_profile_version: int = Field(ge=1)
    expected_learning_state_version: int = Field(ge=1)

    @field_validator("command_id","reason")
    @classmethod
    def trimmed(cls,value: str) -> str:
        if value != value.strip(): raise ValueError("must be trimmed and nonblank")
        return value


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
                         review_service=None, clip_access: ClipAccess | None = None,
                         next_objective_service=None) -> APIRouter:
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
        source = repository.load_summary_source(session_id) if hasattr(repository, "load_summary_source") else None
        evidence_records = aggregate.evidence if source is None else source.evidence
        clip_by_evidence = {item.evidence_id: item for item in aggregate.clips}
        discarded = set(summary.discarded_evidence_ids)
        evidence = []
        for item in evidence_records:
            if item.evidence_id in discarded: continue
            observation = item.observation
            clip = clip_by_evidence.get(item.evidence_id)
            evidence.append({"evidence_id":item.evidence_id,"objective_id":observation.objective_id,
                "outcome":observation.outcome.value,"assistance_level":observation.assistance_level,
                "source_session_id":observation.session_id,
                "stt_confidence":observation.stt_confidence,"response_excerpt":observation.transcribed_response[:240],
                "retention_reason":item.reason_for_retention,"interpretation":item.current_interpretation,
                "interpretation_status":"hypothesis" if item.current_interpretation else None,
                "clip":None if clip is None else {"clip_id":clip.clip_id,"duration_seconds":clip.duration_seconds,"expires_at":clip.expires_at}})
        support = {item.evidence_id:item for item in evidence_records if item.evidence_id not in discarded}
        objective_estimates = []
        for item in aggregate.estimates:
            supported = [support[evidence_id] for evidence_id in item.supporting_evidence_ids if evidence_id in support]
            objective_estimates.append({"objective_id":item.objective_id,"state":item.state.value,
                "estimate_version":item.version,"status":"needs-review" if item.state is CompetencyState.NEEDS_REVIEW else "current-estimate",
                "supporting_evidence_ids":[record.evidence_id for record in supported],
                "support_confidence":{"kind":"stt-confidence-range","minimum":min((record.observation.stt_confidence for record in supported),default=None),"maximum":max((record.observation.stt_confidence for record in supported),default=None)}})
        material_claims = [{"claim_id":item.claim_id,"kind":item.kind,"text":item.text,
            "evidence_ids":list(item.evidence_ids),"status":"hypothesis" if item.is_hypothesis else "fact"} for item in summary.claims]
        profile_proposals = [claim for claim in material_claims if claim["kind"] == "profile-proposal"]
        next_proposals = repository.list_next_objective_proposals(learner_id,source_session_id=session_id) if hasattr(repository,"list_next_objective_proposals") else ()
        def next_wire(item):
            return {"proposal_id":item.proposal_id,"objective_id":item.objective_id,"source_plan_id":item.source_plan_id,
                "source_plan_version":item.source_plan_version,"source_profile_version":item.source_profile_version,"source_learning_state_version":item.source_learning_state_version,"rationale":item.rationale,"evidence_ids":list(item.evidence_ids),
                "status":item.status.value,"revision":item.revision,"created_at":item.created_at.isoformat(),"updated_at":item.updated_at.isoformat()}
        next_history=[]
        if hasattr(repository,"list_next_objective_decisions"):
            for item in next_proposals:
                decisions=repository.list_next_objective_decisions(item.proposal_id)
                for decision in decisions:
                    next_history.append({**next_wire(item),"revision":decision.revision,"status":decision.status.value,
                        "decision_reason":decision.reason,"expected_plan_version":decision.expected_plan_version,
                        "expected_profile_version":decision.expected_profile_version,"expected_learning_state_version":decision.expected_learning_state_version,
                        "resulting_plan_version":decision.resulting_plan_version,"decided_at":decision.decided_at.isoformat()})
                if item.status is ProposalDecisionStatus.STALE and not decisions:
                    next_history.append({**next_wire(item),"decision_reason":"source-snapshot-superseded"})
        else:
            next_history=[next_wire(item) for item in next_proposals if item.status is not ProposalDecisionStatus.PENDING]
        return {"learner":{"learner_id":learner.learner_id,"pseudonym":learner.pseudonym,"age_years":learner.age_years},
            "session_id":session_id,"session_version":summary.source_session_version,"profile_version":summary.source_profile_version,
            "objective_estimates":objective_estimates,"objectives":objective_estimates,
            "assistance_observations":[{"evidence_id":item.evidence_id,"objective_id":item.observation.objective_id,"source_session_id":item.observation.session_id,"assistance_level":item.observation.assistance_level} for item in evidence_records if item.evidence_id not in discarded],
            "authoritative_claims":material_claims,"summary":material_claims,
            "hypotheses":[claim for claim in material_claims if claim["status"] == "hypothesis" and claim["kind"] != "profile-proposal"],
            "profile_change_proposals":profile_proposals,
            "next_objective_proposals":[next_wire(item) for item in next_proposals if item.status is ProposalDecisionStatus.PENDING],
            "next_objective_history":next_history,
            "evidence":evidence,"history":[{"review_id":item.review_id,"version":item.version,"created_at":item.created_at,"action":_wire(item.review)} for item in aggregate.reviews]}

    @router.post("/learners/{learner_id}/sessions/{session_id}/next-objective-proposals/generate", dependencies=auth)
    def generate_next(learner_id: str,session_id: str):
        owned(learner_id,session_id)
        if next_objective_service is None: raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,"next-objective-service-unavailable")
        try: proposal=next_objective_service.propose_available(learner_id,session_id)
        except NextObjectiveError as error: raise HTTPException(status.HTTP_409_CONFLICT,str(error)) from error
        return {"status":"none-available"} if proposal is None else _wire(proposal)

    @router.post("/learners/{learner_id}/sessions/{session_id}/next-objective-proposals/{proposal_id}/decision", dependencies=auth)
    def decide_next(learner_id: str,session_id: str,proposal_id: str,body: NextObjectiveDecisionBody):
        owned(learner_id,session_id)
        if next_objective_service is None: raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,"next-objective-service-unavailable")
        try:
            proposal_id=_safe_id(proposal_id)
            stored = repository.load_next_objective_proposal(proposal_id) if hasattr(repository,"load_next_objective_proposal") else None
            if stored is not None and stored.source_session_id != session_id: raise NextObjectiveError("proposal-owner-mismatch")
            proposal=next_objective_service.decide(NextObjectiveDecision(body.command_id,proposal_id,learner_id,ProposalDecisionStatus(body.decision),body.reason,body.expected_revision,body.expected_plan_version,body.expected_profile_version,body.expected_learning_state_version,datetime.now(timezone.utc)))
        except NextObjectiveError as error:
            reason=str(error); code=status.HTTP_404_NOT_FOUND if reason=="proposal-owner-mismatch" else status.HTTP_409_CONFLICT
            raise HTTPException(code,reason) from error
        return _wire(proposal)

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
