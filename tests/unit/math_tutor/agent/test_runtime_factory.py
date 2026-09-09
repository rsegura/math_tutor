import json
import asyncio
from types import SimpleNamespace

import pytest

from math_tutor.agent.providers.model import OpenResponsesAdapter
from math_tutor.domain.templates import ExpectedAnswerKind
from math_tutor.application.provisioning import DEFAULT_REGULATION_POLICY
from math_tutor.domain.regulation import PedagogicalStrategy, RegulationPolicy
from math_tutor.harness.loop import _parse
from math_tutor.harness.contracts import ToolName
from math_tutor.harness.model import ProviderInvalidResponse, ProviderTimeout


class Responses:
    def __init__(self, response): self.response=response; self.calls=[]
    async def create(self, **kwargs): self.calls.append(kwargs); return self.response


def _matches_schema(value, schema):
    """Small test validator for the schema keywords emitted by this adapter."""
    if "oneOf" in schema and sum(_matches_schema(value, branch) for branch in schema["oneOf"]) != 1:
        return False
    if schema.get("type") == "object":
        if not isinstance(value, dict): return False
        properties=schema.get("properties", {})
        if any(name not in value for name in schema.get("required", ())): return False
        if schema.get("additionalProperties") is False and set(value)-set(properties): return False
        return all(_matches_schema(item, properties[name]) for name,item in value.items() if name in properties)
    if schema.get("type") == "string" and not isinstance(value,str): return False
    if schema.get("type") == "number" and (isinstance(value,bool) or not isinstance(value,(int,float))): return False
    if "const" in schema and value != schema["const"]: return False
    if "enum" in schema and value not in schema["enum"]: return False
    if "minimum" in schema and value < schema["minimum"]: return False
    if "maximum" in schema and value > schema["maximum"]: return False
    return True


def context():
    return SimpleNamespace(
        current_turn=SimpleNamespace(turn_id="turn-1",transcript="son tres decenas",stt_confidence=.9),
        activity=SimpleNamespace(activity_id="activity-1",template_id="place-value-1",objective_id="units-tens",difficulty=2,prompt_es="¿Cuántas decenas?",expected_answer_kind=ExpectedAnswerKind.INTEGER_PAIR,expected_answer_fields=("tens","units"),hints_used=0,attempts_used=0),
        active_objective_ids=("units-tens",),authorised_objective_ids=("units-tens",),
        adaptations=("slow-pace",),duration_minutes=10,max_activities=4,
        activities_used=1,
        regulation_policy=DEFAULT_REGULATION_POLICY,
        regulation_revision=3,
        consecutive_regulation_turns=2,
        regulation_activity_sequence=4,
    )


@pytest.mark.asyncio
async def test_adapter_sends_exact_bounded_tool_contract_and_canonical_answer_shape():
    output=SimpleNamespace(status="completed",error=None,output=[SimpleNamespace(type="function_call",name="record_answer",arguments=json.dumps({"turn_id":"turn-1","answer":{"status":"evaluable","kind":"integer-pair","values":{"tens":3,"units":2}}}))],output_text="")
    responses=Responses(output); adapter=OpenResponsesAdapter(model="pinned",client=SimpleNamespace(responses=responses))
    result=await adapter.complete(prompt="system",context=context(),repair=False,validation_error=None)
    assert result["arguments"]["answer"]["kind"] == "integer-pair"
    assert _parse(result).name is ToolName.RECORD_ANSWER
    call=responses.calls[0]
    assert call["model"] == "pinned"
    record=next(tool for tool in call["tools"] if tool["name"] == "record_answer")
    values=record["parameters"]["properties"]["answer"]["oneOf"][0]["properties"]["values"]
    assert values["required"] == ["tens","units"]
    assert values["additionalProperties"] is False
    assert {tool["name"] for tool in call["tools"]} == {"record_answer","adapt_difficulty","propose_skill_update","regulate_conversation"}
    sent=json.loads(call["input"][1]["content"])
    assert "give_hint" not in sent["allowed_contract"]
    assert sent["adaptations"] == ["slow-pace"]
    assert sent["session_limits"] == {"duration_minutes":10,"max_activities":4}
    assert sent["regulation_state"] == {"revision":3,"consecutive_turns":2,"activity_sequence":4,"max_consecutive_turns":4}


def test_regulation_schema_exposes_only_current_turn_and_authorised_compatible_pairs():
    tools=OpenResponsesAdapter._tools(context())
    regulation=next(tool for tool in tools if tool["name"]=="regulate_conversation")["parameters"]
    assert regulation["type"] == "object"
    assert "additionalProperties" not in regulation
    branches=regulation["oneOf"]
    pairs={(branch["properties"]["signal"]["const"], strategy)
           for branch in branches
           for strategy in branch["properties"]["strategy"]["enum"]}
    assert ("frustrated","validate-emotion") in pairs
    assert ("frustrated","redirect-gently") not in pairs
    assert all(branch["properties"]["turn_id"] == {"const":"turn-1"} for branch in branches)
    assert all(branch["properties"]["confidence"] == {"type":"number","minimum":0,"maximum":1} for branch in branches)
    assert all(branch["required"] == ["turn_id","signal","confidence","strategy"] for branch in branches)


def test_regulation_schema_omits_plan_disallowed_strategies():
    restricted=RegulationPolicy((
        PedagogicalStrategy.SIMPLIFY_LANGUAGE,
        PedagogicalStrategy.REDIRECT_GENTLY,
        PedagogicalStrategy.VALIDATE_EMOTION,
        PedagogicalStrategy.TAKE_SHORT_PAUSE,
    ),3)
    value=context(); value.regulation_policy=restricted
    regulation=next(tool for tool in OpenResponsesAdapter._tools(value) if tool["name"]=="regulate_conversation")["parameters"]
    advertised={strategy for branch in regulation["oneOf"] for strategy in branch["properties"]["strategy"]["enum"]}
    assert advertised == {"simplify-language","redirect-gently","validate-emotion","take-short-pause"}


def test_regulation_json_schema_accepts_authorised_payload_and_rejects_invalid_shapes():
    regulation=next(tool for tool in OpenResponsesAdapter._tools(context()) if tool["name"]=="regulate_conversation")["parameters"]
    valid={"turn_id":"turn-1","signal":"frustrated","confidence":.9,"strategy":"validate-emotion"}
    assert _matches_schema(valid,regulation)
    assert not _matches_schema({**valid,"rationale":"private"},regulation)
    assert not _matches_schema({**valid,"strategy":"redirect-gently"},regulation)


@pytest.mark.asyncio
async def test_repair_request_contains_validation_error_and_same_allowed_contract():
    response=SimpleNamespace(status="completed",error=None,output=[],output_text='{"type":"reply","speech":"Vamos paso a paso.","speech_kind":"social"}')
    responses=Responses(response); adapter=OpenResponsesAdapter(model="pinned",client=SimpleNamespace(responses=responses))
    await adapter.complete(prompt="repair",context=context(),repair=True,validation_error="structured-answer-invalid")
    call=responses.calls[0]
    assert "structured-answer-invalid" in call["input"][1]["content"]
    assert "allowed_contract" in call["input"][1]["content"]


@pytest.mark.asyncio
async def test_adapter_rejects_unstructured_or_unknown_function_output():
    response=SimpleNamespace(status="completed",error=None,output=[SimpleNamespace(type="function_call",name="invented",arguments="{}")],output_text="")
    adapter=OpenResponsesAdapter(model="pinned",client=SimpleNamespace(responses=Responses(response)))
    with pytest.raises(ProviderInvalidResponse): await adapter.complete(prompt="system",context=context(),repair=False,validation_error=None)


def test_record_answer_schema_has_evaluable_and_empty_uncertain_branches():
    tools=OpenResponsesAdapter._tools(context())
    answer=next(tool for tool in tools if tool["name"]=="record_answer")["parameters"]["properties"]["answer"]
    assert len(answer["oneOf"]) == 2
    evaluable,uncertain=answer["oneOf"]
    assert evaluable["properties"]["status"]["const"] == "evaluable"
    assert evaluable["properties"]["kind"]["const"] == "integer-pair"
    assert evaluable["properties"]["values"]["required"] == ["tens","units"]
    assert uncertain["properties"]["kind"]["type"] == "null"
    assert uncertain["properties"]["status"]["enum"] == ["ambiguous","not-evaluable"]
    assert uncertain["properties"]["values"] == {"type":"object","properties":{},"required":[],"additionalProperties":False}


@pytest.mark.asyncio
async def test_adapter_roundtrips_empty_ambiguous_answer_contract():
    payload={"turn_id":"turn-1","answer":{"status":"ambiguous","kind":None,"values":{}}}
    output=SimpleNamespace(status="completed",error=None,output=[SimpleNamespace(type="function_call",name="record_answer",arguments=json.dumps(payload))],output_text="")
    adapter=OpenResponsesAdapter(model="pinned",client=SimpleNamespace(responses=Responses(output)))
    result=await adapter.complete(prompt="system",context=context(),repair=False,validation_error=None)
    proposal=_parse(result)
    assert proposal.name is ToolName.RECORD_ANSWER
    assert proposal.arguments["answer"] == payload["answer"]


@pytest.mark.asyncio
async def test_adapter_timeout_and_caller_cancellation_abort_underlying_request():
    cancelled=[]
    class Blocked:
        async def create(self,**kwargs):
            try: await asyncio.Event().wait()
            finally: cancelled.append(True)
    adapter=OpenResponsesAdapter(model="pinned",client=SimpleNamespace(responses=Blocked()))
    adapter._first_response_seconds=.01
    with pytest.raises(ProviderTimeout): await adapter.complete(prompt="system",context=context(),repair=False)
    assert cancelled == [True]
    task=asyncio.create_task(adapter.complete(prompt="system",context=context(),repair=False))
    await asyncio.sleep(0); task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert cancelled == [True,True]
