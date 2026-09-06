import json
from types import SimpleNamespace

import pytest

from math_tutor.agent.runtime_factory import OpenAIHarnessAdapter
from math_tutor.domain.templates import ExpectedAnswerKind
from math_tutor.harness.loop import _parse
from math_tutor.harness.contracts import ToolName


class Responses:
    def __init__(self, response): self.response=response; self.calls=[]
    def create(self, **kwargs): self.calls.append(kwargs); return self.response


def context():
    return SimpleNamespace(
        current_turn=SimpleNamespace(turn_id="turn-1",transcript="son tres decenas",stt_confidence=.9),
        activity=SimpleNamespace(activity_id="activity-1",template_id="place-value-1",objective_id="units-tens",difficulty=2,prompt_es="¿Cuántas decenas?",expected_answer_kind=ExpectedAnswerKind.INTEGER_PAIR,expected_answer_fields=("tens","units"),hints_used=0,attempts_used=0),
        active_objective_ids=("units-tens",),authorised_objective_ids=("units-tens",),
        adaptations=("slow-pace",),duration_minutes=10,max_activities=4,
        activities_used=1,
    )


def test_adapter_sends_exact_bounded_tool_contract_and_canonical_answer_shape():
    output=SimpleNamespace(output=[SimpleNamespace(type="function_call",name="record_answer",arguments=json.dumps({"turn_id":"turn-1","answer":{"status":"evaluable","kind":"integer-pair","values":{"tens":3,"units":2}}}))],output_text="")
    responses=Responses(output); adapter=OpenAIHarnessAdapter(api_key="x",model="pinned",client=SimpleNamespace(responses=responses))
    result=adapter.complete(prompt="system",context=context(),repair=False,validation_error=None)
    assert result["arguments"]["answer"]["kind"] == "integer-pair"
    assert _parse(result).name is ToolName.RECORD_ANSWER
    call=responses.calls[0]
    assert call["model"] == "pinned"
    record=next(tool for tool in call["tools"] if tool["name"] == "record_answer")
    values=record["parameters"]["properties"]["answer"]["oneOf"][0]["properties"]["values"]
    assert values["required"] == ["tens","units"]
    assert values["additionalProperties"] is False
    assert {tool["name"] for tool in call["tools"]} == {"record_answer","give_hint","adapt_difficulty","propose_skill_update","end_session"}
    sent=json.loads(call["input"][1]["content"])
    assert sent["adaptations"] == ["slow-pace"]
    assert sent["session_limits"] == {"duration_minutes":10,"max_activities":4}


def test_repair_request_contains_validation_error_and_same_allowed_contract():
    response=SimpleNamespace(output=[],output_text='{"type":"reply","speech":"Vamos paso a paso.","speech_kind":"social"}')
    responses=Responses(response); adapter=OpenAIHarnessAdapter(api_key="x",model="pinned",client=SimpleNamespace(responses=responses))
    adapter.complete(prompt="repair",context=context(),repair=True,validation_error="structured-answer-invalid")
    call=responses.calls[0]
    assert "structured-answer-invalid" in call["input"][1]["content"]
    assert "allowed_contract" in call["input"][1]["content"]


def test_adapter_rejects_unstructured_or_unknown_function_output():
    response=SimpleNamespace(output=[SimpleNamespace(type="function_call",name="invented",arguments="{}")],output_text="")
    adapter=OpenAIHarnessAdapter(api_key="x",model="pinned",client=SimpleNamespace(responses=Responses(response)))
    with pytest.raises(ValueError): adapter.complete(prompt="system",context=context(),repair=False,validation_error=None)


def test_record_answer_schema_has_evaluable_and_empty_uncertain_branches():
    tools=OpenAIHarnessAdapter._tools(context())
    answer=next(tool for tool in tools if tool["name"]=="record_answer")["parameters"]["properties"]["answer"]
    assert len(answer["oneOf"]) == 2
    evaluable,uncertain=answer["oneOf"]
    assert evaluable["properties"]["status"]["const"] == "evaluable"
    assert evaluable["properties"]["kind"]["const"] == "integer-pair"
    assert evaluable["properties"]["values"]["required"] == ["tens","units"]
    assert uncertain["properties"]["kind"]["type"] == "null"
    assert uncertain["properties"]["status"]["enum"] == ["ambiguous","not-evaluable"]
    assert uncertain["properties"]["values"] == {"type":"object","properties":{},"required":[],"additionalProperties":False}


def test_adapter_roundtrips_empty_ambiguous_answer_contract():
    payload={"turn_id":"turn-1","answer":{"status":"ambiguous","kind":None,"values":{}}}
    output=SimpleNamespace(output=[SimpleNamespace(type="function_call",name="record_answer",arguments=json.dumps(payload))],output_text="")
    adapter=OpenAIHarnessAdapter(api_key="x",model="pinned",client=SimpleNamespace(responses=Responses(output)))
    result=adapter.complete(prompt="system",context=context(),repair=False,validation_error=None)
    proposal=_parse(result)
    assert proposal.name is ToolName.RECORD_ANSWER
    assert proposal.arguments["answer"] == payload["answer"]
