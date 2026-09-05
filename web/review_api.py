"""Minimum authenticated review read used by the Task-11 composition gate."""

import hmac
from fastapi import APIRouter, Header, HTTPException, status


def create_review_router(repository, therapist_token: str | None) -> APIRouter:
    router = APIRouter()

    @router.get("/api/review/sessions/{session_id}")
    def session_state(session_id: str, authorization: str | None = Header(default=None)):
        if not therapist_token or authorization is None or not hmac.compare_digest(authorization, f"Bearer {therapist_token}"):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unauthorised")
        state = repository.load_state(session_id)
        if state is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "session-not-found")
        return {
            "session_id": state.session.session_id,
            "learner_id": state.session.learner_id,
            "plan_id": state.session.plan_id,
            "plan_version": state.session.plan_version,
            "active": state.session.can_continue,
            "objective_ids": state.session.authorised_objective_ids,
        }

    return router

