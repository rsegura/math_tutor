import json
import asyncio
from types import SimpleNamespace

import pytest

from math_tutor.agent.providers.model import OpenResponsesAdapter, build_model_adapter
from math_tutor.harness.model import ProviderInvalidResponse, ProviderRateLimited, ProviderTimeout, ProviderUpstreamUnavailable
from math_tutor.agent.providers.settings import ProviderSettings
from math_tutor.domain.templates import ExpectedAnswerKind


class Responses:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def settings(provider="openai", base_url=None):
    return ProviderSettings(
        stt_provider="deepgram", stt_model="nova", stt_api_key="stt",
        llm_provider=provider, llm_model="provider/model", llm_api_key="secret-key",
        llm_base_url=base_url, tts_provider="elevenlabs", tts_model="turbo",
        tts_voice_id=None, tts_api_key="tts",
    )


def context():
    return SimpleNamespace(
        current_turn=SimpleNamespace(turn_id="turn-1", transcript="tres y dos", stt_confidence=.9),
        activity=SimpleNamespace(activity_id="activity-1", template_id="place-value-1", objective_id="units-tens", difficulty=2, prompt_es="Pregunta", expected_answer_kind=ExpectedAnswerKind.INTEGER_PAIR, expected_answer_fields=("tens", "units"), hints_used=0, attempts_used=0),
        active_objective_ids=("units-tens",), authorised_objective_ids=("units-tens",),
        adaptations=("slow-pace",), duration_minutes=10, max_activities=4, activities_used=1,
    )


def test_factory_builds_explicit_openai_and_openrouter_clients():
    calls = []
    def client_factory(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(responses=Responses({"status": "completed", "output": [], "output_text": "{}"}))

    openai = build_model_adapter(settings(), client_factory=client_factory)
    router = build_model_adapter(settings("openrouter"), client_factory=client_factory)

    assert isinstance(openai, OpenResponsesAdapter)
    assert isinstance(router, OpenResponsesAdapter)
    assert calls == [
        {"api_key": "secret-key", "max_retries": 0},
        {"api_key": "secret-key", "max_retries": 0, "base_url": "https://openrouter.ai/api/v1"},
    ]


@pytest.mark.asyncio
async def test_openrouter_fixture_translates_canonical_function_call():
    fixture = {
        "id": "resp_router_1", "status": "completed", "error": None,
        "output": [{"type": "function_call", "name": "record_answer", "arguments": json.dumps({"turn_id": "turn-1", "answer": {"status": "evaluable", "kind": "integer-pair", "values": {"tens": 3, "units": 2}}})}],
        "output_text": "",
    }
    adapter = OpenResponsesAdapter(client=SimpleNamespace(responses=Responses(fixture)), model="provider/model")
    result = await adapter.complete(prompt="system", context=context(), repair=False)
    assert result["type"] == "tool"
    assert result["name"] == "record_answer"
    assert result["arguments"]["answer"]["values"] == {"tens": 3, "units": 2}
    assert adapter._client.responses.calls[0]["max_output_tokens"] == 256


@pytest.mark.asyncio
async def test_reasoning_item_is_ignored_alongside_one_function_call():
    fixture = {"status":"completed","error":None,"output":[
        {"type":"reasoning","id":"rs_1","summary":[]},
        {"type":"function_call","name":"give_hint","arguments":"{}"},
    ]}
    adapter=OpenResponsesAdapter(client=SimpleNamespace(responses=Responses(fixture)),model="provider/model")
    assert (await adapter.complete(prompt="system",context=context(),repair=False)) == {"type":"tool","name":"give_hint","arguments":{}}


@pytest.mark.asyncio
async def test_openrouter_fixture_translates_canonical_json_text():
    fixture = {"status": "completed", "error": None, "output": [{"type":"message","role":"assistant","content":[{"type":"output_text","text":"canonical"}]}], "output_text": '{"type":"reply","speech":"Vamos paso a paso.","speech_kind":"social"}'}
    adapter = OpenResponsesAdapter(client=SimpleNamespace(responses=Responses(fixture)), model="provider/model")
    assert (await adapter.complete(prompt="system", context=context(), repair=False))["type"] == "reply"


@pytest.mark.asyncio
async def test_reasoning_item_is_ignored_alongside_one_message():
    fixture={"status":"completed","error":None,"output":[
        {"type":"reasoning","id":"rs_1","summary":[]},
        {"type":"message","role":"assistant","content":[{"type":"output_text","text":"Vamos paso a paso."}]},
    ],"output_text":'{"type":"reply","speech":"Vamos paso a paso.","speech_kind":"social"}'}
    adapter=OpenResponsesAdapter(client=SimpleNamespace(responses=Responses(fixture)),model="provider/model")
    assert (await adapter.complete(prompt="system",context=context(),repair=False))["type"] == "reply"


@pytest.mark.asyncio
async def test_adapter_closes_client_once():
    class Client:
        def __init__(self): self.responses=Responses({"status":"completed","output":[],"output_text":"{}"}); self.closes=0
        async def close(self): self.closes += 1
    client=Client(); adapter=OpenResponsesAdapter(client=client,model="provider/model")
    await adapter.aclose(); await adapter.aclose()
    assert client.closes == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("first_error", [RuntimeError("close failed"), asyncio.CancelledError()])
async def test_failed_or_cancelled_close_can_be_retried(first_error):
    class Client:
        def __init__(self): self.responses=Responses({}); self.closes=0
        async def close(self):
            self.closes += 1
            if self.closes == 1: raise first_error
    client=Client(); adapter=OpenResponsesAdapter(client=client,model="provider/model")
    with pytest.raises(type(first_error)):
        await adapter.aclose()
    await adapter.aclose()
    assert client.closes == 2


@pytest.mark.asyncio
async def test_adapter_supports_aclose_and_never_double_closes_client():
    class Client:
        def __init__(self): self.responses=Responses({}); self.acloses=0; self.closes=0
        async def aclose(self): self.acloses += 1
        async def close(self): self.closes += 1
    client=Client(); adapter=OpenResponsesAdapter(client=client,model="provider/model")
    await adapter.aclose(); await adapter.aclose()
    assert (client.acloses,client.closes) == (1,0)


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture", [
    {"output": [], "output_text": "{}"},
    {"status": "failed", "error": {"message": "secret transcript"}, "output": []},
    {"status": "incomplete", "incomplete_details": {"reason": "secret prompt"}, "output": []},
    {"status": "completed", "error": {"message": "secret body"}, "output": []},
    {"status": "completed", "output": [{"type": "function_call", "name": "give_hint", "arguments": "{}"}, {"type": "function_call", "name": "end_session", "arguments": "{}"}]},
    {"status": "completed", "output": [{"type": "function_call", "name": "give_hint", "arguments": "{}"}, {"type": "message", "content": []}]},
    {"status": "completed", "output": [{"type": "mystery"}]},
    {"status": "completed", "output": [{"type": "function_call", "name": "give_hint", "arguments": "[]"}]},
])
async def test_provider_response_failures_are_closed_and_sanitized(fixture):
    adapter = OpenResponsesAdapter(client=SimpleNamespace(responses=Responses(fixture)), model="provider/model")
    with pytest.raises(ProviderInvalidResponse) as caught:
        await adapter.complete(prompt="secret prompt", context=context(), repair=False)
    message = str(caught.value)
    assert "secret" not in message
    assert "transcript" not in message


@pytest.mark.asyncio
async def test_provider_transport_error_is_sanitized():
    class Broken:
        async def create(self, **kwargs):
            raise RuntimeError("secret-key and child transcript")
    adapter = OpenResponsesAdapter(client=SimpleNamespace(responses=Broken()), model="provider/model")
    with pytest.raises(ProviderUpstreamUnavailable) as caught:
        await adapter.complete(prompt="system", context=context(), repair=False)
    assert "secret-key" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("error,expected", [
    (TimeoutError("secret transcript"), ProviderTimeout),
    (type("RateLimitError", (Exception,), {"status_code": 429})("secret body"), ProviderRateLimited),
    (type("APIStatusError", (Exception,), {"status_code": 503})("secret cause"), ProviderUpstreamUnavailable),
])
async def test_sdk_failures_map_to_closed_neutral_categories(error, expected):
    class Broken:
        async def create(self, **kwargs): raise error
    adapter = OpenResponsesAdapter(client=SimpleNamespace(responses=Broken()), model="provider/model")
    with pytest.raises(expected) as caught:
        await adapter.complete(prompt="secret prompt", context=context(), repair=False)
    assert str(caught.value) == expected.code
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.asyncio
async def test_invalid_provider_wire_shape_has_neutral_category():
    adapter = OpenResponsesAdapter(
        client=SimpleNamespace(responses=Responses({"status":"completed","output":"secret body"})),
        model="provider/model",
    )
    with pytest.raises(ProviderInvalidResponse) as caught:
        await adapter.complete(prompt="secret prompt", context=context(), repair=False)
    assert str(caught.value) == "provider-invalid-response"
