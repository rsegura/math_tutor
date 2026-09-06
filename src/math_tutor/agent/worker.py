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
from math_tutor.infrastructure.clip_retention import AsyncConsentGate, ClipRetentionService, RetentionSettings, RetentionSweeper
from math_tutor.infrastructure.evidence_clips import LiveAudioFrameSink, OpaqueClipStore, SessionAudioBuffers


_RETENTION_RUNTIME_KEY = "math_tutor_retention_runtime"


class WorkerRetentionRuntime:
    """The single retention owner for one LiveKit worker process."""
    def __init__(self, repository, service, sweeper, settings=None) -> None:
        self.repository, self.service, self.sweeper = repository, service, sweeper
        self.settings = settings
        self._started = False
        self._closed = False

    @classmethod
    def from_environment(cls, env) -> "WorkerRetentionRuntime":
        database_path = Path(env.get("DATABASE_PATH", "/app/data/math_tutor.db"))
        migrate(database_path)
        repository = SQLiteTutoringRepository(database_path)
        settings = RetentionSettings.from_environment(env)
        service = ClipRetentionService(repository, OpaqueClipStore(settings.evidence_directory), settings)
        return cls(repository, service, RetentionSweeper(service.maintenance, interval_seconds=settings.sweep_interval_seconds), settings)

    def startup_before_jobs(self) -> None:
        self.service.reconcile_startup()
        self.service.maintenance()

    async def start_periodic(self) -> None:
        if not self._started:
            await self.sweeper.start()
            self._started = True

    async def aclose(self) -> None:
        if self._closed:
            return
        await self.sweeper.aclose()
        self.repository.close()
        self._closed = True


def prewarm_retention(process) -> None:
    """Synchronous prewarm runs before LiveKit advertises the job process."""
    runtime = WorkerRetentionRuntime.from_environment(os.environ)
    runtime.startup_before_jobs()
    process.userdata[_RETENTION_RUNTIME_KEY] = runtime


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
    process_runtime = ctx.proc.userdata.get(_RETENTION_RUNTIME_KEY)
    if not isinstance(process_runtime, WorkerRetentionRuntime):
        raise RuntimeError("retention runtime was not prewarmed")
    await process_runtime.start_periodic()
    repository = process_runtime.repository
    retention_settings = process_runtime.settings
    clip_retention = process_runtime.service
    session_id = metadata.tutoring_session_id
    buffers = None
    consent_gate = None
    async def close_retention() -> None:
        if buffers is not None:
            buffers.close_session(session_id)
        if consent_gate is not None:
            await consent_gate.aclose()
        await process_runtime.aclose()
    ctx.add_shutdown_callback(close_retention)
    # Exact durable reconstruction is deliberately before provider creation or room connection.
    runtime = build_tutoring_runtime(metadata=metadata, repository=repository, env=os.environ)
    snapshot_id = runtime.bootstrap.audio_consent_snapshot_id
    def durable_consent_check() -> bool:
        return bool(snapshot_id and repository.authorize_session_clip_buffer(
            learner_id=runtime.bootstrap.learner.learner_id,
            session_id=session_id, snapshot_id=snapshot_id, at=datetime.now(timezone.utc)))
    buffers = SessionAudioBuffers(context_seconds=retention_settings.context_seconds,
        hard_cap_seconds=retention_settings.hard_cap_seconds, enabled=retention_settings.enabled,
        authorize=lambda candidate_session_id, at: bool(consent_gate and consent_gate.allows_capture()))
    consent_gate = AsyncConsentGate(durable_consent_check,
        initially_enabled=retention_settings.enabled and runtime.bootstrap.clip_capture_enabled,
        on_revoked=lambda: buffers.disable_session(session_id))
    await start_consent_gate_before_connect(consent_gate)
    engine = BoundedConversationEngine(repository=repository, runtime=runtime, curricula_dir=Path(__file__).resolve().parents[1] / "curricula")
    stt, tts = create_voice_providers(runtime.providers)
    await ctx.connect()
    session = AgentSession(stt=stt, llm=SilentLLM(), tts=tts, vad=silero.VAD.load(), preemptive_generation=False)
    speech_handles = SpeechHandleTracker()
    session.on("speech_created", speech_handles.observe)
    session_id = runtime.bootstrap.session.session_id
    capture_frame = LiveAudioFrameSink(buffers, session_id, clock=lambda: datetime.now(timezone.utc))
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


async def start_consent_gate_before_connect(consent_gate: AsyncConsentGate) -> None:
    """Require one durable authorization before any audio surface is connected."""
    await consent_gate.refresh_once()
    await consent_gate.start()


def build_worker_options() -> WorkerOptions:
    return WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm_retention, agent_name=AGENT_NAME)


if __name__ == "__main__":
    cli.run_app(build_worker_options())
