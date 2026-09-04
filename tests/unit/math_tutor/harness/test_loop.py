from dataclasses import replace

import pytest

from math_tutor.application.results import CommandResult, CommandStatus
from math_tutor.harness.context import ActivityContext, LearnerState, TurnEvidence, build_harness_context
from math_tutor.harness.limits import HarnessLimits
from math_tutor.harness.loop import HarnessBudgetExceeded, PedagogicalHarness
from math_tutor.harness.registry import PedagogicalToolRegistry


class FakeService:
    def __init__(self): self.commands = []
    def stop_now(self, command):
        self.commands.append(command)
        return CommandResult(command.command_id, CommandStatus.APPLIED, "applied")


class FakeModel:
    def __init__(self, outputs): self.outputs = list(outputs); self.calls = []
    def complete(self, *, prompt, context, repair):
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


def test_unverified_mathematical_speech_is_never_released(context):
    model = FakeModel([{"type": "reply", "speech": "Cuatro más cuatro son ocho.", "speech_kind": "mathematical"}] * 2)
    with pytest.raises(HarnessBudgetExceeded) as caught:
        PedagogicalHarness(model, PedagogicalToolRegistry(FakeService(), HarnessLimits()), HarnessLimits()).run(context)
    assert "mathematical-speech-requires-fence" in str(caught.value.__cause__)


def test_model_cannot_bypass_math_fence_by_mislabeling_speech_as_social(context):
    model = FakeModel([{"type": "reply", "speech": "Cuatro más cuatro son ocho.", "speech_kind": "social"}] * 2)
    with pytest.raises(HarnessBudgetExceeded) as caught:
        PedagogicalHarness(model, PedagogicalToolRegistry(FakeService(), HarnessLimits()), HarnessLimits()).run(context)
    assert "reply-not-reviewed" in str(caught.value.__cause__)


def test_explicit_model_budget_is_hard(context):
    model = FakeModel([{"bad": True}])
    with pytest.raises(HarnessBudgetExceeded, match="model-call-budget"):
        PedagogicalHarness(model, PedagogicalToolRegistry(FakeService(), HarnessLimits()), HarnessLimits(max_model_calls=1)).run(context)


def test_tool_budget_is_exactly_one_step():
    with pytest.raises(ValueError, match="max_tool_steps"):
        HarnessLimits(max_tool_steps=2)
