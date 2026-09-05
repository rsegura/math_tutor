"""LiveKit worker for an already authorised mathematics tutoring session."""

from __future__ import annotations

import os
from pathlib import Path

from livekit.agents import AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import silero

from math_tutor.agent.runtime_factory import BoundedConversationEngine, build_tutoring_runtime, create_voice_providers
from math_tutor.agent.voice_agent import HarnessVoiceAgent, SilentLLM
from math_tutor.agent.lifecycle import TTSWatchdog, TerminalCloser
from math_tutor.infrastructure.dispatch import AGENT_NAME, DispatchMetadata
from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository


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
        f"Adaptaciones: {adaptations}. Respeta inmediatamente cualquier petición de parar."
    )


async def entrypoint(ctx: JobContext) -> None:
    metadata = resolve_dispatch(ctx)
    database_path = Path(os.environ.get("DATABASE_PATH", "/app/data/math_tutor.db"))
    migrate(database_path)
    repository = SQLiteTutoringRepository(database_path)
    # Exact durable reconstruction is deliberately before provider creation or room connection.
    runtime = build_tutoring_runtime(metadata=metadata, repository=repository, env=os.environ)
    engine = BoundedConversationEngine(repository=repository, runtime=runtime, curricula_dir=Path(__file__).resolve().parents[1] / "curricula")
    stt, tts = create_voice_providers(runtime.providers)
    await ctx.connect()
    session = AgentSession(stt=stt, llm=SilentLLM(), tts=tts, vad=silero.VAD.load(), preemptive_generation=False)
    agent = HarnessVoiceAgent(instructions=_instructions(runtime), initial_prompt=engine.initial_prompt, decide=engine.decide, cancel=engine.cancel, tts_watchdog=TTSWatchdog())
    closer = TerminalCloser(ctx, session)
    agent.bind_terminal_closer(closer)
    ctx.add_shutdown_callback(closer.aclose)
    await session.start(agent=agent, room=ctx.room)


def build_worker_options() -> WorkerOptions:
    return WorkerOptions(entrypoint_fnc=entrypoint, agent_name=AGENT_NAME)


if __name__ == "__main__":
    cli.run_app(build_worker_options())
