"""Strict provider configuration and durable runtime reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping

from math_tutor.infrastructure.dispatch import DispatchMetadata, VoiceBootstrap
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs
from math_tutor.application.service import PedagogicalMutationPolicy, SelectNextActivity, TutoringService
from math_tutor.application.session_runtime import SessionRuntime
from math_tutor.domain.evidence import TranscriptionReliabilityPolicy
from math_tutor.domain.learning import AssistanceThreshold, CompetencyState, ProgressionPolicy
from math_tutor.harness.context import ActivityContext, LearnerState, TurnEvidence, build_harness_context
from math_tutor.harness.limits import HarnessLimits
from math_tutor.harness.loop import PedagogicalHarness
from math_tutor.harness.registry import PedagogicalToolRegistry
from math_tutor.agent.voice_agent import VoiceDecision, VoiceTurn, is_stop_request


class ProviderConfigError(ValueError):
    pass


_SUPPORTED = {
    "STT_PROVIDER": frozenset({"deepgram", "openai"}),
    "LLM_PROVIDER": frozenset({"openai"}),
    "TTS_PROVIDER": frozenset({"elevenlabs", "openai"}),
}


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if not value or not value.strip():
        raise ProviderConfigError(f"{name} is required")
    return value.strip()


@dataclass(frozen=True, slots=True)
class ProviderSettings:
    stt_provider: str
    stt_model: str
    stt_api_key: str
    llm_provider: str
    llm_model: str
    llm_api_key: str
    tts_provider: str
    tts_model: str
    tts_voice_id: str
    tts_api_key: str

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> "ProviderSettings":
        values = {name: _required(env, name) for name in (
            "STT_PROVIDER", "STT_MODEL", "STT_API_KEY", "LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY",
            "TTS_PROVIDER", "TTS_MODEL", "TTS_VOICE_ID", "TTS_API_KEY",
        )}
        for field, supported in _SUPPORTED.items():
            if values[field] not in supported:
                raise ProviderConfigError(f"unsupported {field}: {values[field]!r}")
        return cls(*(values[name] for name in (
            "STT_PROVIDER", "STT_MODEL", "STT_API_KEY", "LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY",
            "TTS_PROVIDER", "TTS_MODEL", "TTS_VOICE_ID", "TTS_API_KEY",
        )))


@dataclass(frozen=True, slots=True)
class TutoringVoiceRuntime:
    bootstrap: VoiceBootstrap
    providers: ProviderSettings


def build_tutoring_runtime(*, metadata: DispatchMetadata, repository, env: Mapping[str, str]) -> TutoringVoiceRuntime:
    """Durable authorisation precedes all provider configuration/activity."""
    bootstrap = repository.reconstruct_voice_runtime(metadata)
    settings = ProviderSettings.from_environment(env)
    return TutoringVoiceRuntime(bootstrap, settings)


def create_voice_providers(settings: ProviderSettings):
    """Instantiate only the explicitly configured, pinned provider choices."""
    if settings.stt_provider == "deepgram":
        from livekit.plugins import deepgram
        stt = deepgram.STT(api_key=settings.stt_api_key, model=settings.stt_model, language="es")
    elif settings.stt_provider == "openai":
        from livekit.plugins import openai as openai_plugin
        stt = openai_plugin.STT(api_key=settings.stt_api_key, model=settings.stt_model, language="es")
    else:  # guarded by ProviderSettings, kept fail-closed for forged instances
        raise ProviderConfigError("unsupported STT provider")
    from livekit.plugins import openai as openai_plugin
    if settings.tts_provider == "elevenlabs":
        from livekit.plugins import elevenlabs
        tts = elevenlabs.TTS(api_key=settings.tts_api_key, model=settings.tts_model, voice_id=settings.tts_voice_id, language="es")
    elif settings.tts_provider == "openai":
        tts = openai_plugin.TTS(api_key=settings.tts_api_key, model=settings.tts_model, voice=settings.tts_voice_id)
    else:
        raise ProviderConfigError("unsupported TTS provider")
    return stt, tts


class OpenAIHarnessAdapter:
    """Unstructured provider output; the bounded harness validates and repairs it."""
    def __init__(self, *, api_key: str, model: str) -> None:
        from openai import OpenAI
        self._client = OpenAI(api_key=api_key, max_retries=0)
        self._model = model

    def complete(self, *, prompt: str, context, repair: bool) -> object:
        compact = {
            "turn_id": context.current_turn.turn_id,
            "transcript": context.current_turn.transcript,
            "activity_id": context.activity.activity_id,
            "prompt_es": context.activity.prompt_es,
            "expected_answer_kind": context.activity.objective_id,
            "active_objectives": context.active_objective_ids,
            "hints_used": context.activity.hints_used,
        }
        response = self._client.responses.create(
            model=self._model,
            input=[{"role":"system","content":prompt},{"role":"user","content":json.dumps(compact, ensure_ascii=False)}],
        )
        try:
            return json.loads(response.output_text)
        except (json.JSONDecodeError, TypeError, AttributeError) as error:
            raise ValueError("model-output-must-be-json") from error


class BoundedConversationEngine:
    def __init__(self, *, repository, runtime: TutoringVoiceRuntime, curricula_dir: Path) -> None:
        curriculum, templates = load_curriculum_catalogs(curricula_dir / "primary-math-v1.yaml", curricula_dir / "activity-templates-v1.yaml")
        self._repository = repository
        self._runtime = SessionRuntime()
        thresholds = tuple(AssistanceThreshold(state, 3) for state in (
            CompetencyState.EXPLORING, CompetencyState.WITH_INTENSIVE_HELP,
            CompetencyState.WITH_LIGHT_HELP, CompetencyState.INDEPENDENT, CompetencyState.GENERALIZED,
        ))
        hints = {hint.id: hint.text for objective in curriculum.objectives for hint in objective.hints}
        self._service = TutoringService(repository, self._runtime, templates, TranscriptionReliabilityPolicy(0.65), ProgressionPolicy(thresholds), PedagogicalMutationPolicy(), hints)
        limits = HarnessLimits()
        model = OpenAIHarnessAdapter(api_key=runtime.providers.llm_api_key, model=runtime.providers.llm_model)
        self._harness = PedagogicalHarness(model, PedagogicalToolRegistry(self._service, limits), limits)
        self._bootstrap = runtime.bootstrap
        self._curriculum = curriculum
        self._templates = templates
        self._initial_activity_id = "activity-1"
        self._ensure_initial_activity()

    def _ensure_initial_activity(self) -> None:
        aggregate = self._repository.load_session_aggregate(self._bootstrap.session.session_id)
        if aggregate is None:
            return
        if aggregate.activities:
            selected = next((event.activity_id for event in reversed(aggregate.events) if event.kind == "activity-selected"), None)
            if selected is None:
                raise RuntimeError("persisted activity has no selection event")
            self._initial_activity_id = selected
            return
        objective_id = aggregate.session.active_objective_ids[0]
        template = self._templates.for_objective(objective_id)[0]
        generation = self._runtime.start_generation(aggregate.session.session_id)
        result = self._service.select_next_activity(SelectNextActivity(
            command_id=f"bootstrap:{aggregate.session.session_id}", session_id=aggregate.session.session_id,
            expected_session_version=aggregate.session.version, expected_profile_version=aggregate.profile_version,
            generation_id=generation.generation_id, activity_id="activity-1", objective_id=objective_id,
            template_id=template.id, seed=1, difficulty=template.difficulty_min,
        ))
        if getattr(result.status, "value", None) != "applied":
            raise RuntimeError(f"initial activity rejected: {result.reason}")

    @property
    def initial_prompt(self) -> str:
        activity = self._repository.load_activity(self._bootstrap.session.session_id, self._initial_activity_id)
        if activity is None:
            raise RuntimeError("initial activity is missing")
        return activity.prompt_es

    def cancel(self) -> None:
        self._runtime.cancel_generation(self._bootstrap.session.session_id)

    def decide(self, turn: VoiceTurn) -> VoiceDecision:
        aggregate = self._repository.load_session_aggregate(self._bootstrap.session.session_id)
        if aggregate is None or aggregate.session.ended:
            raise RuntimeError("session is no longer active")
        activity_id = next((event.activity_id for event in reversed(aggregate.events) if event.kind == "activity-selected"), None)
        if activity_id is None:
            raise RuntimeError("active activity is missing")
        activity = self._repository.load_activity(aggregate.session.session_id, activity_id)
        progress = next((item for item in aggregate.activity_progress if item.activity_id == activity_id), None)
        objective = self._curriculum.objective(activity.objective_id)
        hint_text_by_id = {hint.id: hint.text for hint in objective.hints}
        generation = self._runtime.start_generation(aggregate.session.session_id)
        context = build_harness_context(
            max_history_turns=4, session_id=aggregate.session.session_id, learner_id=aggregate.session.learner_id,
            expected_session_version=aggregate.session.version, expected_profile_version=aggregate.profile_version,
            generation_id=generation.generation_id, authorised_objective_ids=aggregate.session.authorised_objective_ids,
            active_objective_ids=aggregate.session.active_objective_ids,
            activity=ActivityContext(activity_id, activity.template_id, activity.objective_id, activity.difficulty, activity.prompt_es,
                activity.hint_ids, tuple(hint_text_by_id[hint_id] for hint_id in activity.hint_ids), progress.hints_used if progress else 0, progress.attempts_used if progress else 0),
            learner_state=LearnerState(tuple((estimate.objective_id, estimate.state.value) for estimate in aggregate.estimates),
                (aggregate.plan.presentation.language_style, aggregate.plan.presentation.instruction_length)),
            recent_history=(), current_turn=TurnEvidence(turn.turn_id, turn.text, turn.confidence or 0.0),
        )
        decision = self._harness.run(context, stop_requested=is_stop_request(turn.text))
        speech = "De acuerdo, paramos aquí." if decision.terminal else (decision.speech or "Vamos paso a paso.")
        return VoiceDecision(speech, terminal=decision.terminal, reason="stop-requested" if decision.terminal else decision.reason)
