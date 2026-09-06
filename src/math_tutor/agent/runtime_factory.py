"""Strict provider configuration and durable runtime reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Mapping
import asyncio

from math_tutor.infrastructure.dispatch import DispatchMetadata, VoiceBootstrap
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs
from math_tutor.application.service import EndSession, PedagogicalMutationPolicy, SelectNextActivity, TutoringService
from math_tutor.application.session_runtime import SessionRuntime
from math_tutor.domain.evidence import ObservationOutcome, TranscriptionReliabilityPolicy
from math_tutor.domain.learning import AssistanceThreshold, CompetencyState, ProgressionPolicy
from math_tutor.harness.context import ActivityContext, LearnerState, TurnEvidence, build_harness_context
from math_tutor.harness.contracts import ToolName
from math_tutor.harness.limits import HarnessLimits
from math_tutor.harness.loop import PedagogicalHarness
from math_tutor.harness.registry import PedagogicalToolRegistry, SessionCapExceeded
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
    llm_first_response_seconds: float = 4.0
    llm_total_seconds: float = 10.0

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> "ProviderSettings":
        values = {name: _required(env, name) for name in (
            "STT_PROVIDER", "STT_MODEL", "STT_API_KEY", "LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY",
            "TTS_PROVIDER", "TTS_MODEL", "TTS_VOICE_ID", "TTS_API_KEY",
        )}
        for field, supported in _SUPPORTED.items():
            if values[field] not in supported:
                raise ProviderConfigError(f"unsupported {field}: {values[field]!r}")
        first, total = (_deadline(env, "LLM_FIRST_RESPONSE_SECONDS", 4.0), _deadline(env, "LLM_TOTAL_SECONDS", 10.0))
        if first > total: raise ProviderConfigError("LLM first response deadline must not exceed total deadline")
        return cls(*(values[name] for name in (
            "STT_PROVIDER", "STT_MODEL", "STT_API_KEY", "LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY",
            "TTS_PROVIDER", "TTS_MODEL", "TTS_VOICE_ID", "TTS_API_KEY",
        )), first, total)


def _deadline(env: Mapping[str,str], name: str, default: float) -> float:
    try: value=float(env.get(name,str(default)))
    except (TypeError,ValueError): raise ProviderConfigError(f"{name} must be numeric") from None
    if not 2 <= value <= 15: raise ProviderConfigError(f"{name} must be between 2 and 15 seconds")
    return value


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
    def __init__(self, *, api_key: str, model: str, client=None, first_response_seconds: float = 4.0) -> None:
        if not 2 <= first_response_seconds <= 15: raise ProviderConfigError("LLM first response deadline must be between 2 and 15 seconds")
        if client is None:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=api_key, max_retries=0)
        self._client = client
        self._model = model
        self._first_response_seconds = first_response_seconds

    @staticmethod
    def _tools(context) -> list[dict[str, object]]:
        answer_kind = context.activity.expected_answer_kind.value
        answer_fields = list(context.activity.expected_answer_fields)
        if context.activity.expected_answer_kind.value == "relation":
            value_schema = {"type":"string","enum":["greater","less","equal"]}
        elif context.activity.expected_answer_kind.value == "integer-sequence":
            value_schema = {"type":"array","items":{"type":"integer"}}
        else:
            value_schema = {"type":"integer"}
        value_properties = {field: dict(value_schema) for field in answer_fields}
        answer = {"oneOf":[
            {"type":"object","additionalProperties":False,"properties":{
                "status":{"const":"evaluable"}, "kind":{"const":answer_kind},
                "values":{"type":"object","properties":value_properties,"required":answer_fields,"additionalProperties":False},
            },"required":["status","kind","values"]},
            {"type":"object","additionalProperties":False,"properties":{
                "status":{"type":"string","enum":["ambiguous","not-evaluable"]},
                "kind":{"type":"null"},
                "values":{"type":"object","properties":{},"required":[],"additionalProperties":False},
            },"required":["status","kind","values"]},
        ]}
        schemas = {
            "record_answer": ({"turn_id":{"type":"string"},"answer":answer}, ["turn_id","answer"]),
            "give_hint": ({}, []),
            "adapt_difficulty": ({"objective_id":{"type":"string","enum":list(context.active_objective_ids)},"difficulty":{"type":"integer"},"seed":{"type":"integer"},"activity_id":{"type":"string"}}, ["objective_id","difficulty","seed","activity_id"]),
            "propose_skill_update": ({"objective_id":{"type":"string","enum":list(context.authorised_objective_ids)}}, ["objective_id"]),
            "end_session": ({"reason":{"type":"string"}}, []),
        }
        # Realtime/provider tool arguments are not trusted as structured output;
        # exact schemas guide generation and the harness remains authoritative.
        return [{"type":"function","name":name,"description":"Acción pedagógica validada por el harness.","parameters":{"type":"object","properties":properties,"required":required,"additionalProperties":False},"strict":False} for name,(properties,required) in schemas.items()]

    async def complete(self, *, prompt: str, context, repair: bool, validation_error: str | None = None) -> object:
        tools = self._tools(context)
        compact = {
            "turn_id": context.current_turn.turn_id,
            "transcript": context.current_turn.transcript,
            "activity_id": context.activity.activity_id,
            "prompt_es": context.activity.prompt_es,
            "template_id": context.activity.template_id,
            "objective_id": context.activity.objective_id,
            "difficulty": context.activity.difficulty,
            "expected_answer_kind": context.activity.expected_answer_kind.value,
            "expected_answer_fields": context.activity.expected_answer_fields,
            "active_objectives": context.active_objective_ids,
            "authorised_objectives": context.authorised_objective_ids,
            "adaptations": context.adaptations,
            "session_limits":{"duration_minutes":context.duration_minutes,"max_activities":context.max_activities},
            "hints_used": context.activity.hints_used,
            "attempts_used": context.activity.attempts_used,
            "activities_used": context.activities_used,
            "validation_error": None if validation_error is None else {"code":validation_error},
            "allowed_contract": {tool["name"]: tool["parameters"] for tool in tools},
        }
        async with asyncio.timeout(self._first_response_seconds):
            response = await self._client.responses.create(model=self._model,input=[{"role":"system","content":prompt},{"role":"user","content":json.dumps(compact, ensure_ascii=False)}],tools=tools,tool_choice="auto",parallel_tool_calls=False)
        calls = [item for item in getattr(response, "output", ()) if getattr(item, "type", None) == "function_call"]
        if calls:
            if len(calls) != 1 or calls[0].name not in {tool["name"] for tool in tools}:
                raise ValueError("model-output-has-invalid-tool-call")
            try:
                arguments = json.loads(calls[0].arguments)
            except (json.JSONDecodeError, TypeError) as error:
                raise ValueError("model-tool-arguments-must-be-json") from error
            if not isinstance(arguments, dict):
                raise ValueError("model-tool-arguments-must-be-object")
            return {"type":"tool","name":calls[0].name,"arguments":arguments}
        try:
            return json.loads(response.output_text)
        except (json.JSONDecodeError, TypeError, AttributeError) as error:
            raise ValueError("model-output-must-be-json") from error


class BoundedConversationEngine:
    def __init__(self, *, repository, runtime: TutoringVoiceRuntime, curricula_dir: Path, now=None, model=None) -> None:
        curriculum, templates = load_curriculum_catalogs(curricula_dir / "primary-math-v1.yaml", curricula_dir / "activity-templates-v1.yaml")
        self._repository = repository
        self._runtime = SessionRuntime()
        self._now = now or (lambda: datetime.now(timezone.utc))
        thresholds = tuple(AssistanceThreshold(state, 3) for state in (
            CompetencyState.EXPLORING, CompetencyState.WITH_INTENSIVE_HELP,
            CompetencyState.WITH_LIGHT_HELP, CompetencyState.INDEPENDENT, CompetencyState.GENERALIZED,
        ))
        hints = {hint.id: hint.text for objective in curriculum.objectives for hint in objective.hints}
        plan_limits = runtime.bootstrap.plan.limits
        per_activity_cap = min(3, plan_limits.max_activities)
        limits = HarnessLimits(max_model_calls=2, max_tool_steps=1, max_hints_per_activity=per_activity_cap,
            max_attempts_per_activity=per_activity_cap, min_difficulty=1, max_difficulty=5)
        self._service = TutoringService(repository, self._runtime, templates, TranscriptionReliabilityPolicy(0.65), ProgressionPolicy(thresholds),
            PedagogicalMutationPolicy(max_attempts_per_activity=limits.max_attempts_per_activity,max_hints_per_activity=limits.max_hints_per_activity,min_repeated_outcomes_for_adaptation=2), hints)
        self._bootstrap = runtime.bootstrap
        self._curriculum = curriculum
        self._templates = templates
        self._initial_activity_id = "activity-1"
        self.startup_terminal_reason = self._cap_reason(self._repository.load_session_aggregate(self._bootstrap.session.session_id))
        if self.startup_terminal_reason is not None:
            self._stop(self.startup_terminal_reason)
            self._harness = None
            return
        self._ensure_initial_activity()
        model = model or OpenAIHarnessAdapter(api_key=runtime.providers.llm_api_key, model=runtime.providers.llm_model, first_response_seconds=runtime.providers.llm_first_response_seconds)
        self._harness = PedagogicalHarness(model, PedagogicalToolRegistry(self._service, limits, guard=self._runtime_cap_reason), limits)
        self._llm_total_seconds = runtime.providers.llm_total_seconds

    def _runtime_cap_reason(self) -> str | None:
        return self._cap_reason(self._repository.load_session_aggregate(self._bootstrap.session.session_id))

    def _cap_reason(self, aggregate) -> str | None:
        if aggregate is None:
            raise RuntimeError("session state is missing")
        started = self._bootstrap.session_started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        now = self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        if now >= started + timedelta(minutes=self._bootstrap.plan.limits.duration_minutes):
            return "duration-cap-reached"
        completed = sum(1 for item in aggregate.activity_progress if item.attempts_used > 0)
        if len(aggregate.activities) > self._bootstrap.plan.limits.max_activities:
            return "activity-cap-reached"
        if completed >= self._bootstrap.plan.limits.max_activities:
            return "activity-cap-reached"
        return None

    def _stop(self, reason: str) -> None:
        state = self._repository.load_state(self._bootstrap.session.session_id)
        if state is None or state.session.ended:
            return
        generation = self._runtime.start_generation(state.session.session_id)
        result = self._service.stop_now(EndSession(command_id=f"cap:{state.session.session_id}:{state.session.version}",
            session_id=state.session.session_id,expected_session_version=state.session.version,expected_profile_version=state.profile_version,
            generation_id=generation.generation_id,reason=reason))
        if getattr(result.status,"value",None) != "applied":
            raise RuntimeError(f"session cap stop rejected: {result.reason}")

    def force_stop(self, reason: str) -> None:
        """Idempotent durable terminal mutation used by transport fail-safes."""
        self._stop(reason)

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
        if self.startup_terminal_reason is not None:
            return "La sesión ha terminado por hoy."
        activity = self._repository.load_activity(self._bootstrap.session.session_id, self._initial_activity_id)
        if activity is None:
            raise RuntimeError("initial activity is missing")
        return activity.prompt_es

    def cancel(self) -> None:
        self._runtime.cancel_generation(self._bootstrap.session.session_id)

    def _next_activity_after_correct(self, *, turn_id: str, source_activity_id: str, source_activity) -> str:
        """Persist and return the reviewed follow-up selected from trusted state."""
        session_id = self._bootstrap.session.session_id
        transition_key = f"{session_id}:{source_activity.template_id}:{turn_id}"
        digest = sha256(transition_key.encode("utf-8")).hexdigest()
        activity_id = f"activity-{digest[:16]}"
        existing = self._repository.load_activity(session_id, activity_id)
        if existing is not None:
            return existing.prompt_es
        state = self._repository.load_state(session_id)
        if state is None or state.session.ended:
            raise RuntimeError("session is no longer active")
        generation = self._runtime.start_generation(session_id)
        result = self._service.select_next_activity(SelectNextActivity(
            command_id=f"follow-up:{digest}", session_id=session_id,
            expected_session_version=state.session.version,
            expected_profile_version=state.profile_version,
            generation_id=generation.generation_id,
            activity_id=activity_id,
            source_activity_id=source_activity_id,
            objective_id=source_activity.objective_id,
            template_id=source_activity.template_id,
            seed=int(digest[16:24], 16),
            difficulty=source_activity.difficulty,
        ))
        if getattr(result.status, "value", None) not in {"applied", "replayed"}:
            raise RuntimeError(f"follow-up activity rejected: {result.reason}")
        if not hasattr(result.payload, "prompt_es"):
            raise RuntimeError("follow-up activity has no canonical prompt")
        return result.payload.prompt_es

    async def decide(self, turn: VoiceTurn) -> VoiceDecision:
        aggregate = self._repository.load_session_aggregate(self._bootstrap.session.session_id)
        if aggregate is None or aggregate.session.ended:
            raise RuntimeError("session is no longer active")
        prior_observation = self._repository.load_observation(
            aggregate.session.session_id, f"obs-{turn.turn_id}"
        )
        if (
            prior_observation is not None
            and prior_observation.observation.outcome is ObservationOutcome.CORRECT
        ):
            source_id = prior_observation.observation.activity_id
            source = self._repository.load_activity(aggregate.session.session_id, source_id)
            if source is None:
                raise RuntimeError("recorded answer activity is missing")
            next_prompt = self._next_activity_after_correct(
                turn_id=turn.turn_id,
                source_activity_id=source_id,
                source_activity=source,
            )
            return VoiceDecision(f"Sí, esa respuesta es correcta. {next_prompt}")
        cap = self._cap_reason(aggregate)
        if cap is not None:
            self._stop(cap)
            return VoiceDecision("La sesión ha terminado por hoy.",terminal=True,reason=cap)
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
                activity.hint_ids, tuple(hint_text_by_id[hint_id] for hint_id in activity.hint_ids), progress.hints_used if progress else 0, progress.attempts_used if progress else 0,
                activity.expected_answer.kind, tuple(activity.expected_answer.values)),
            learner_state=LearnerState(tuple((estimate.objective_id, estimate.state.value) for estimate in aggregate.estimates),
                (aggregate.plan.presentation.language_style, aggregate.plan.presentation.instruction_length)),
            recent_history=(), current_turn=TurnEvidence(turn.turn_id, turn.text, turn.confidence or 0.0),
            adaptations=self._bootstrap.plan.adaptations, duration_minutes=self._bootstrap.plan.limits.duration_minutes,
            max_activities=self._bootstrap.plan.limits.max_activities, activities_used=len(aggregate.activities),
        )
        try:
            decision = await self._harness.run_async(context, stop_requested=is_stop_request(turn.text), total_seconds=self._llm_total_seconds)
        except SessionCapExceeded as error:
            self._stop(str(error))
            return VoiceDecision("La sesión ha terminado por hoy.",terminal=True,reason=str(error))
        except asyncio.CancelledError:
            self.cancel()
            raise
        except Exception:
            self.cancel()
            reason = self._runtime_cap_reason() or "llm-provider-unavailable"
            self._stop(reason)
            return VoiceDecision("No puedo continuar ahora. Terminamos por hoy.",terminal=True,reason=reason)
        cap = self._runtime_cap_reason()
        if cap is not None:
            self._stop(cap)
            return VoiceDecision("La sesión ha terminado por hoy.",terminal=True,reason=cap)
        if decision.applied_tool is ToolName.RECORD_ANSWER and decision.reason == "answer-correct":
            next_prompt = self._next_activity_after_correct(
                turn_id=turn.turn_id,
                source_activity_id=activity_id,
                source_activity=activity,
            )
            return VoiceDecision(f"{decision.speech} {next_prompt}")
        speech = "De acuerdo, paramos aquí." if decision.terminal else (decision.speech or "Vamos paso a paso.")
        return VoiceDecision(speech, terminal=decision.terminal, reason="stop-requested" if decision.terminal else decision.reason)
