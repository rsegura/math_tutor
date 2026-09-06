"""LiveKit worker for an already authorised mathematics tutoring session."""

from __future__ import annotations

import os
from pathlib import Path
from datetime import datetime, timezone

from livekit.agents import AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import silero

from math_tutor.agent.runtime_factory import BoundedConversationEngine, build_tutoring_runtime, create_voice_providers
from math_tutor.agent.voice_agent import HarnessVoiceAgent, SilentLLM
from math_tutor.agent.lifecycle import SpeechHandleTracker, StaticFallbackAudioPlayer, TTSWatchdog, TerminalCloser
from math_tutor.infrastructure.dispatch import AGENT_NAME, DispatchMetadata
from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository
from math_tutor.infrastructure.clip_retention import ClipRetentionService, RetentionSettings, RetentionSweeper
from math_tutor.infrastructure.evidence_clips import AudioFrame, OpaqueClipStore, SessionAudioBuffers


class RetentionLifecycle:
    """Makes startup cleanup and awaited shutdown explicit at composition roots."""
    def __init__(self, sweeper) -> None:
        self.sweeper = sweeper

    async def start_before_jobs(self) -> None:
        await self.sweeper.sweep_once()
        await self.sweeper.start()

    async def aclose(self) -> None:
        await self.sweeper.aclose()


def resolve_dispatch(ctx: JobContext) -> DispatchMetadata:
    return DispatchMetadata.parse(ctx.job.metadata, ctx.job.room.name)


def _instructions(runtime) -> str:
    learner = runtime.bootstrap.learner
    plan = runtime.bootstrap.plan
    objectives = ", ".join(plan.plan.active_objective_ids)
    adaptations = ", ".join(plan.adaptations) or "ninguna"
    return (
        "Eres un tutor de matemáticas de primaria que habla español. "
        "Trabajas únicamente dentro de las acciones validadas por el harness pedagógico. "
        f"Dirígete al alumno como {learner.pseudonym}. Estilo: {plan.plan.presentation.language_style}; "
        f"instrucciones: {plan.plan.presentation.instruction_length}. Objetivos autorizados: {objectives}. "
        f"Adaptaciones: {adaptations}. Límites: {plan.limits.duration_minutes} minutos y "
        f"{plan.limits.max_activities} actividades. Respeta inmediatamente cualquier petición de parar."
    )


async def entrypoint(ctx: JobContext) -> None:
    metadata = resolve_dispatch(ctx)
    database_path = Path(os.environ.get("DATABASE_PATH", "/app/data/math_tutor.db"))
    migrate(database_path)
    repository = SQLiteTutoringRepository(database_path)
    retention_settings = RetentionSettings.from_environment(os.environ)
    clip_retention = ClipRetentionService(repository, OpaqueClipStore(retention_settings.evidence_directory), retention_settings)
    retention_lifecycle = RetentionLifecycle(RetentionSweeper(clip_retention.sweep_expired, interval_seconds=retention_settings.sweep_interval_seconds))
    await retention_lifecycle.start_before_jobs()
    session_id = metadata.tutoring_session_id
    buffers = SessionAudioBuffers(context_seconds=retention_settings.context_seconds, hard_cap_seconds=retention_settings.hard_cap_seconds)
    async def close_retention() -> None:
        buffers.close_session(session_id)
        await retention_lifecycle.aclose()
    ctx.add_shutdown_callback(close_retention)
    # Exact durable reconstruction is deliberately before provider creation or room connection.
    runtime = build_tutoring_runtime(metadata=metadata, repository=repository, env=os.environ)
    engine = BoundedConversationEngine(repository=repository, runtime=runtime, curricula_dir=Path(__file__).resolve().parents[1] / "curricula")
    stt, tts = create_voice_providers(runtime.providers)
    await ctx.connect()
    session = AgentSession(stt=stt, llm=SilentLLM(), tts=tts, vad=silero.VAD.load(), preemptive_generation=False)
    speech_handles = SpeechHandleTracker()
    session.on("speech_created", speech_handles.observe)
    session_id = runtime.bootstrap.session.session_id
    def capture_frame(frame) -> None:
        if not retention_settings.enabled:
            return
        frame = getattr(frame, "frame", frame)
        rate = getattr(frame, "sample_rate", 0)
        samples = getattr(frame, "samples_per_channel", 0)
        duration = float(samples) / float(rate) if rate and samples else float(getattr(frame, "duration", 0))
        payload = bytes(getattr(frame, "data", b""))
        if payload and duration > 0:
            buffers.append(session_id, AudioFrame(payload, duration, datetime.now(timezone.utc)))
    def persist_selection(turn_id: str, evidence_id: str) -> None:
        del turn_id
        clip_retention.persist_selected(evidence_id=evidence_id, learner_id=runtime.bootstrap.learner.learner_id,
            session_id=session_id, consent_snapshot_id=runtime.bootstrap.audio_consent_snapshot_id,
            selected=buffers.select(session_id, datetime.now(timezone.utc)), evidence_selection_committed=True)
    agent = HarnessVoiceAgent(instructions=_instructions(runtime), initial_prompt=engine.initial_prompt, decide=engine.decide, cancel=engine.cancel, tts_watchdog=TTSWatchdog(), initial_terminal_reason=engine.startup_terminal_reason, force_stop=engine.force_stop, fallback_audio=StaticFallbackAudioPlayer(session), terminal_handle=speech_handles.latest, audio_frame_sink=capture_frame, evidence_selected=persist_selection)
    closer = TerminalCloser(ctx, session)
    agent.bind_terminal_closer(closer)
    ctx.add_shutdown_callback(closer.aclose)
    await session.start(agent=agent, room=ctx.room)


def build_worker_options() -> WorkerOptions:
    return WorkerOptions(entrypoint_fnc=entrypoint, agent_name=AGENT_NAME)


if __name__ == "__main__":
    cli.run_app(build_worker_options())
