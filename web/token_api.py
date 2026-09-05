"""Learner token exchange for an already provisioned tutoring session."""

from __future__ import annotations

from datetime import datetime, timezone
import os

from fastapi import APIRouter, HTTPException, Response, status
from livekit import api
from pydantic import BaseModel, ConfigDict, Field, field_validator

from math_tutor.infrastructure.dispatch import AGENT_NAME, DispatchMetadata, VoiceBootstrapError, room_name_for


class TokenBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tutoring_session_id: str = Field(min_length=1, max_length=128)
    join_code: str = Field(min_length=1, max_length=256)

    @field_validator("tutoring_session_id", "join_code")
    @classmethod
    def trimmed(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("must be trimmed")
        return value


def create_token_router(repository, *, env=None, clock=None) -> APIRouter:
    environment = os.environ if env is None else env
    now = (lambda: datetime.now(timezone.utc)) if clock is None else clock
    router = APIRouter()

    @router.post("/api/token")
    def exchange(body: TokenBody, response: Response):
        response.headers["Cache-Control"] = "no-store"
        try:
            api_key = environment["LIVEKIT_API_KEY"]
            api_secret = environment["LIVEKIT_API_SECRET"]
        except KeyError as error:
            raise RuntimeError(f"missing LiveKit server setting: {error.args[0]}") from error
        if not api_key.strip() or not api_secret.strip():
            raise RuntimeError("LiveKit server credentials must be nonempty")
        try:
            bootstrap = repository.authorise_learner_join(body.tutoring_session_id, body.join_code, now=now())
        except VoiceBootstrapError as error:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error)) from error
        session = bootstrap.session
        metadata = DispatchMetadata(session.session_id, session.version, session.plan_id, session.plan_version)
        room_name = room_name_for(session.session_id)
        token = (
                api.AccessToken(api_key, api_secret)
                .with_identity(f"learner-{session.session_id}")
                .with_grants(api.VideoGrants(room_join=True, room=room_name))
                .with_room_config(api.RoomConfiguration(agents=[api.RoomAgentDispatch(agent_name=AGENT_NAME, metadata=metadata.to_json())]))
                .to_jwt()
            )
        return {
            "tutoring_session_id": session.session_id,
            "room_name": room_name,
            "participant_token": token,
            "server_url": environment.get("LIVEKIT_PUBLIC_URL", "ws://localhost:7880"),
        }

    return router
