"""Vendor adapters for the provider-neutral harness model port."""
from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Mapping

from math_tutor.agent.providers.settings import ProviderConfigError, ProviderSettings
from math_tutor.domain.regulation import ConversationalSignal
from math_tutor.harness.model import (
    ProviderFailure, ProviderInvalidResponse, ProviderRateLimited,
    ProviderRequestRejected, ProviderTimeout, ProviderUpstreamUnavailable,
)


def _request_failure(error: BaseException) -> ProviderFailure | ProviderRequestRejected | None:
    """Classify only stable SDK type/status metadata, never its text or body."""
    status = getattr(error, "status_code", None)
    name = type(error).__name__
    from_openai_sdk = type(error).__module__.split(".", 1)[0] == "openai"
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)) or (from_openai_sdk and name == "APITimeoutError"):
        return ProviderTimeout()
    if from_openai_sdk and (name == "RateLimitError" or status == 429):
        return ProviderRateLimited()
    if from_openai_sdk and name == "APIConnectionError":
        return ProviderUpstreamUnavailable()
    if from_openai_sdk and isinstance(status, int) and 500 <= status <= 599:
        return ProviderUpstreamUnavailable()
    if from_openai_sdk and isinstance(status, int) and 400 <= status <= 499:
        return ProviderRequestRejected()
    return None

def _field(value: object, name: str, default=None):
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


class OpenResponsesAdapter:
    """Translate the common OpenResponses wire contract into harness objects."""

    def __init__(self, *, client, model: str, first_response_seconds: float = 4.0, max_output_tokens: int = 256) -> None:
        if not 2 <= first_response_seconds <= 15:
            raise ProviderConfigError("LLM first response deadline must be between 2 and 15 seconds")
        self._client = client
        self._model = model
        self._first_response_seconds = first_response_seconds
        self._max_output_tokens = max_output_tokens
        self._close_lock = asyncio.Lock()
        self._closed = False

    async def aclose(self) -> None:
        """Idempotently release the provider HTTP client."""
        async with self._close_lock:
            if self._closed:
                return
            close = getattr(self._client, "aclose", None) or getattr(self._client, "close", None)
            if close is None:
                self._closed = True
                return
            result = close()
            if inspect.isawaitable(result):
                await result
            self._closed = True

    @staticmethod
    def _tools(context) -> list[dict[str, object]]:
        answer_kind = context.activity.expected_answer_kind.value
        answer_fields = list(context.activity.expected_answer_fields)
        if answer_kind == "relation":
            value_schema = {"type": "string", "enum": ["greater", "less", "equal"]}
        elif answer_kind == "integer-sequence":
            value_schema = {"type": "array", "items": {"type": "integer"}}
        else:
            value_schema = {"type": "integer"}
        value_properties = {field: dict(value_schema) for field in answer_fields}
        answer = {"oneOf": [
            {"type": "object", "additionalProperties": False, "properties": {
                "status": {"const": "evaluable"}, "kind": {"const": answer_kind},
                "values": {"type": "object", "properties": value_properties, "required": answer_fields, "additionalProperties": False},
            }, "required": ["status", "kind", "values"]},
            {"type": "object", "additionalProperties": False, "properties": {
                "status": {"type": "string", "enum": ["ambiguous", "not-evaluable"]},
                "kind": {"type": "null"},
                "values": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            }, "required": ["status", "kind", "values"]},
        ]}
        schemas = {
            "record_answer": ({"turn_id": {"type": "string"}, "answer": answer}, ["turn_id", "answer"]),
            "give_hint": ({}, []),
            "adapt_difficulty": ({"objective_id": {"type": "string", "enum": list(context.active_objective_ids)}, "difficulty": {"type": "integer"}, "seed": {"type": "integer"}, "activity_id": {"type": "string"}}, ["objective_id", "difficulty", "seed", "activity_id"]),
            "propose_skill_update": ({"objective_id": {"type": "string", "enum": list(context.authorised_objective_ids)}}, ["objective_id"]),
            "end_session": ({"reason": {"type": "string"}}, []),
        }
        tools = [{"type": "function", "name": name, "description": "Acción pedagógica validada por el harness.", "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}, "strict": False} for name, (properties, required) in schemas.items()]
        policy = getattr(context, "regulation_policy", None)
        if policy is not None:
            branches = []
            for signal in ConversationalSignal:
                strategies = policy.allowed_for(signal)
                if strategies:
                    branches.append({
                        "type": "object",
                        "properties": {
                            "turn_id": {"const": context.current_turn.turn_id},
                            "signal": {"const": signal.value},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "strategy": {"type": "string", "enum": [strategy.value for strategy in strategies]},
                        },
                        "required": ["turn_id", "signal", "confidence", "strategy"],
                        "additionalProperties": False,
                    })
            if branches:
                tools.append({"type": "function", "name": "regulate_conversation", "description": "Señal conversacional provisional y estrategia autorizada; no diagnostica ni evalúa matemáticas.", "parameters": {"type": "object", "oneOf": branches, "additionalProperties": False}, "strict": False})
        return tools

    async def complete(self, *, prompt: str, context, repair: bool, validation_error: str | None = None) -> object:
        tools = self._tools(context)
        compact = {
            "turn_id": context.current_turn.turn_id, "transcript": context.current_turn.transcript,
            "activity_id": context.activity.activity_id, "prompt_es": context.activity.prompt_es,
            "template_id": context.activity.template_id, "objective_id": context.activity.objective_id,
            "difficulty": context.activity.difficulty, "expected_answer_kind": context.activity.expected_answer_kind.value,
            "expected_answer_fields": context.activity.expected_answer_fields,
            "active_objectives": context.active_objective_ids, "authorised_objectives": context.authorised_objective_ids,
            "adaptations": context.adaptations,
            "session_limits": {"duration_minutes": context.duration_minutes, "max_activities": context.max_activities},
            "hints_used": context.activity.hints_used, "attempts_used": context.activity.attempts_used,
            "activities_used": context.activities_used,
            "regulation_policy": None if context.regulation_policy is None else {"allowed_strategies": [strategy.value for strategy in context.regulation_policy.allowed_strategies]},
            "regulation_state": {"revision": context.regulation_revision, "consecutive_turns": context.consecutive_regulation_turns, "activity_sequence": context.regulation_activity_sequence, "max_consecutive_turns": None if context.regulation_policy is None else context.regulation_policy.max_consecutive_regulation_turns},
            "validation_error": None if validation_error is None else {"code": validation_error},
            "allowed_contract": {tool["name"]: tool["parameters"] for tool in tools},
        }
        request_failure = None
        request_error = None
        try:
            async with asyncio.timeout(self._first_response_seconds):
                response = await self._client.responses.create(
                    model=self._model, input=[{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps(compact, ensure_ascii=False)}],
                    tools=tools, tool_choice="auto", parallel_tool_calls=False,
                    max_output_tokens=self._max_output_tokens,
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            request_failure = _request_failure(error)
            request_error = error
        if request_failure is not None:
            raise request_failure from None
        if request_error is not None:
            raise request_error
        status = _field(response, "status")
        if status != "completed" or _field(response, "error") is not None:
            raise ProviderInvalidResponse() from None
        output = _field(response, "output", ())
        if not isinstance(output, (list, tuple)):
            raise ProviderInvalidResponse() from None
        calls = [item for item in output if _field(item, "type") == "function_call"]
        messages = [item for item in output if _field(item, "type") == "message"]
        unexpected = [item for item in output if _field(item, "type") not in {"function_call", "message", "reasoning"}]
        if calls:
            if len(calls) != 1 or messages or unexpected:
                raise ProviderInvalidResponse() from None
            call = calls[0]
            allowed = {tool["name"] for tool in tools}
            name = _field(call, "name")
            if name not in allowed:
                raise ProviderInvalidResponse() from None
            try:
                arguments = json.loads(_field(call, "arguments"))
            except (json.JSONDecodeError, TypeError):
                raise ProviderInvalidResponse() from None
            if not isinstance(arguments, dict):
                raise ProviderInvalidResponse() from None
            return {"type": "tool", "name": name, "arguments": arguments}
        if unexpected or len(messages) > 1:
            raise ProviderInvalidResponse() from None
        try:
            result = json.loads(_field(response, "output_text"))
        except (json.JSONDecodeError, TypeError):
            raise ProviderInvalidResponse() from None
        if not isinstance(result, dict):
            raise ProviderInvalidResponse() from None
        return result


def build_model_adapter(settings: ProviderSettings, *, client_factory=None) -> OpenResponsesAdapter:
    if client_factory is None:
        from openai import AsyncOpenAI
        client_factory = AsyncOpenAI
    kwargs = {"api_key": settings.llm_api_key, "max_retries": 0}
    if settings.llm_provider == "openrouter":
        kwargs["base_url"] = settings.llm_base_url or "https://openrouter.ai/api/v1"
    elif settings.llm_provider != "openai":
        raise ProviderConfigError("unsupported LLM provider")
    return OpenResponsesAdapter(
        client=client_factory(**kwargs),
        model=settings.llm_model,
        first_response_seconds=settings.llm_first_response_seconds,
        max_output_tokens=settings.llm_max_output_tokens,
    )
