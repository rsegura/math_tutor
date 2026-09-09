from dataclasses import replace

import pytest

from math_tutor.application.results import CommandResult, CommandStatus
from math_tutor.domain.regulation import ConversationalSignal, PedagogicalStrategy
from math_tutor.harness.context import ActivityContext, LearnerState, TurnEvidence, build_harness_context
from math_tutor.harness.limits import HarnessLimits
from math_tutor.harness.loop import HarnessContractExhausted, HarnessProposalInvalid, PedagogicalHarness, _parse
from math_tutor.harness.registry import PedagogicalToolRegistry
from math_tutor.harness.model import ProviderInvalidResponse, ProviderUpstreamUnavailable
from math_tutor.harness.contracts import ToolName


class FakeService:
    def __init__(self): self.commands = []
    def stop_now(self, command):
        self.commands.append(command)
        return CommandResult(command.command_id, CommandStatus.APPLIED, "applied")


class FakeModel:
    def __init__(self, outputs): self.outputs = list(outputs); self.calls = []
    def complete(self, *, prompt, context, repair, validation_error=None):
        self.calls.append((prompt, context, repair))
        return self.outputs.pop(0)


@pytest.fixture
def context():
    return build_harness_context(
        session_id="s", learner_id="l", expected_session_version=1, expected_profile_version=1,
        generation_id="gen-1", authorised_objective_ids=("units-tens",), active_objective_ids=("units-tens",),
        activity=ActivityContext("a", "t", "units-tens", 1, "Pregunta", (), (), 0),
        learner_state=LearnerState((("units-tens", "exploring"),), ("short-instructions",)),
        recent_history=(), current_turn=TurnEvidence("turn", "Para, por favor", 0.9), max_history_turns=3,
    )


def test_stop_request_bypasses_model_and_ends_immediately(context):
    service = FakeService(); model = FakeModel([])
    decision = PedagogicalHarness(model, PedagogicalToolRegistry(service, HarnessLimits()), HarnessLimits()).run(context, stop_requested=True)
    assert model.calls == []
    assert type(service.commands[-1]).__name__ == "EndSession"
    assert decision.terminal


def test_invalid_output_gets_exactly_one_bounded_repair(context):
    model = FakeModel([{"bad": True}, {"type": "reply", "speech": "Vamos paso a paso.", "speech_kind": "social"}])
    decision = PedagogicalHarness(model, PedagogicalToolRegistry(FakeService(), HarnessLimits()), HarnessLimits(max_model_calls=2)).run(context)
    assert decision.speech == "Vamos paso a paso."
    assert len(model.calls) == 2 and model.calls[1][2] is True


def test_sync_model_end_session_proposal_is_repaired_without_executing_stop(context):
    service=FakeService()
    model=FakeModel([
        {"type":"tool","name":"end_session","arguments":{"reason":"model-refusal"}},
        {"type":"reply","speech":"Vamos paso a paso.","speech_kind":"social"},
    ])
    decision=PedagogicalHarness(model,PedagogicalToolRegistry(service,HarnessLimits()),HarnessLimits()).run(context)
    assert decision.speech == "Vamos paso a paso."
    assert service.commands == []
    assert len(model.calls) == 2 and model.calls[1][2] is True


def test_unverified_mathematical_speech_is_never_released(context):
    model = FakeModel([{"type": "reply", "speech": "Cuatro más cuatro son ocho.", "speech_kind": "mathematical"}] * 2)
    with pytest.raises(HarnessContractExhausted) as caught:
        PedagogicalHarness(model, PedagogicalToolRegistry(FakeService(), HarnessLimits()), HarnessLimits()).run(context)
    assert str(caught.value.__cause__) == "harness-proposal-invalid"


def test_model_cannot_bypass_math_fence_by_mislabeling_speech_as_social(context):
    model = FakeModel([{"type": "reply", "speech": "Cuatro más cuatro son ocho.", "speech_kind": "social"}] * 2)
    with pytest.raises(HarnessContractExhausted) as caught:
        PedagogicalHarness(model, PedagogicalToolRegistry(FakeService(), HarnessLimits()), HarnessLimits()).run(context)
    assert str(caught.value.__cause__) == "harness-proposal-invalid"


def test_explicit_model_budget_is_hard(context):
    model = FakeModel([{"bad": True}])
    with pytest.raises(HarnessContractExhausted, match="harness-contract-exhausted"):
        PedagogicalHarness(model, PedagogicalToolRegistry(FakeService(), HarnessLimits()), HarnessLimits(max_model_calls=1)).run(context)


def test_tool_budget_is_exactly_one_step():
    with pytest.raises(ValueError, match="max_tool_steps"):
        HarnessLimits(max_tool_steps=2)


def test_sync_run_rejects_declared_async_adapter_without_invoking_it(context):
    class AsyncModel:
        def __init__(self): self.called = False
        async def complete(self, **kwargs): self.called = True
    model = AsyncModel()
    with pytest.raises(TypeError, match="run_async"):
        PedagogicalHarness(model, PedagogicalToolRegistry(FakeService(), HarnessLimits()), HarnessLimits()).run(context)
    assert model.called is False


def test_sync_run_closes_unexpected_coroutine(context):
    async def result():
        return {"bad": True}
    class NominallySync:
        def __init__(self): self.returned = None
        def complete(self, **kwargs):
            self.returned = result()
            return self.returned
    model = NominallySync()
    with pytest.raises(TypeError, match="run_async"):
        PedagogicalHarness(model, PedagogicalToolRegistry(FakeService(), HarnessLimits()), HarnessLimits()).run(context)
    assert model.returned.cr_frame is None


@pytest.mark.asyncio
async def test_async_invalid_proposal_gets_one_repair_then_closed_exhaustion(context):
    model = FakeModel([{"bad":"secret transcript"}, {"also":"secret response"}])
    harness = PedagogicalHarness(model, PedagogicalToolRegistry(FakeService(), HarnessLimits()), HarnessLimits())
    with pytest.raises(HarnessContractExhausted) as caught:
        await harness.run_async(context)
    assert len(model.calls) == 2
    assert model.calls[1][2] is True
    assert isinstance(caught.value.last_failure, HarnessProposalInvalid)
    assert "secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_async_model_end_session_proposal_is_repaired_without_executing_stop(context):
    service=FakeService()
    model=FakeModel([
        {"type":"tool","name":"end_session","arguments":{}},
        {"type":"reply","speech":"Gracias por decírmelo.","speech_kind":"social"},
    ])
    decision=await PedagogicalHarness(model,PedagogicalToolRegistry(service,HarnessLimits()),HarnessLimits()).run_async(context)
    assert decision.speech == "Gracias por decírmelo."
    assert service.commands == []
    assert len(model.calls) == 2 and model.calls[1][2] is True


@pytest.mark.asyncio
async def test_provider_invalid_response_gets_one_repair(context):
    class Model(FakeModel):
        def complete(self, **kwargs):
            self.calls.append((kwargs["prompt"], kwargs["context"], kwargs["repair"]))
            value=self.outputs.pop(0)
            if isinstance(value, Exception): raise value
            return value
    model=Model([ProviderInvalidResponse(), {"type":"reply","speech":"Vamos paso a paso.","speech_kind":"social"}])
    decision=await PedagogicalHarness(model,PedagogicalToolRegistry(FakeService(),HarnessLimits()),HarnessLimits()).run_async(context)
    assert decision.speech == "Vamos paso a paso."
    assert [call[2] for call in model.calls] == [False,True]


@pytest.mark.asyncio
async def test_provider_availability_failure_aborts_without_repair(context):
    class Model(FakeModel):
        def complete(self, **kwargs):
            self.calls.append((kwargs["prompt"], kwargs["context"], kwargs["repair"]))
            raise ProviderUpstreamUnavailable()
    model=Model([])
    with pytest.raises(ProviderUpstreamUnavailable):
        await PedagogicalHarness(model,PedagogicalToolRegistry(FakeService(),HarnessLimits()),HarnessLimits()).run_async(context)
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_post_fence_rejection_aborts_without_repair(context):
    class Registry:
        def execute(self, proposal, context):
            from math_tutor.harness.registry import ToolRejected
            raise ToolRejected("secret persisted rejection", crossed_fence=True)
    model=FakeModel([{"type":"tool","name":"give_hint","arguments":{}}, {"type":"reply","speech":"Vamos paso a paso.","speech_kind":"social"}])
    with pytest.raises(HarnessProposalInvalid):
        await PedagogicalHarness(model,Registry(),HarnessLimits()).run_async(context)
    assert len(model.calls) == 1


def test_regulation_tool_accepts_only_the_exact_typed_current_turn_contract():
    proposal = _parse({
        "type": "tool",
        "name": "regulate_conversation",
        "arguments": {
            "turn_id": "turn",
            "signal": "frustrated",
            "confidence": 0.82,
            "strategy": "validate-emotion",
        },
    })
    assert proposal.name is ToolName.REGULATE_CONVERSATION
    assert dict(proposal.arguments) == {
        "turn_id": "turn",
        "signal": "frustrated",
        "confidence": 0.82,
        "strategy": "validate-emotion",
    }


@pytest.mark.parametrize(
    "arguments",
    [
        {"turn_id": "turn", "signal": "engaged", "confidence": 0.9, "strategy": "repeat-instruction"},
        {"turn_id": "turn", "signal": "confused", "confidence": True, "strategy": "repeat-instruction"},
        {"turn_id": "turn", "signal": "confused", "confidence": float("nan"), "strategy": "repeat-instruction"},
        {"turn_id": "turn", "signal": "confused", "confidence": 0.9, "strategy": "invent-example"},
        {"turn_id": "turn", "signal": "off-task", "confidence": 0.9, "strategy": "give-ordered-hint"},
        {"turn_id": "turn", "signal": ConversationalSignal.CONFUSED, "confidence": 0.9, "strategy": "repeat-instruction"},
        {"turn_id": "turn", "signal": "confused", "confidence": 0.9, "strategy": PedagogicalStrategy.REPEAT_INSTRUCTION},
        {"turn_id": "turn", "signal": "confused", "confidence": 0.9, "strategy": "repeat-instruction", "rationale": "free form"},
        {"signal": "confused", "confidence": 0.9, "strategy": "repeat-instruction"},
    ],
)
def test_regulation_tool_rejects_unknown_malformed_or_extra_fields(arguments):
    with pytest.raises(ValueError, match="invalid-tool-output"):
        _parse({"type": "tool", "name": "regulate_conversation", "arguments": arguments})
