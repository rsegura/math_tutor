from dataclasses import replace

import pytest

from math_tutor.application.results import CommandResult, CommandStatus
from math_tutor.application.service import CanonicalHintResult, RecordAnswerResult
from math_tutor.domain.evidence import ObservationOutcome
from math_tutor.domain.mathematics import AnswerCheck, AnswerOutcome
from math_tutor.harness.context import ActivityContext, LearnerState, TurnEvidence, build_harness_context
from math_tutor.harness.contracts import ToolName, ToolProposal
from math_tutor.harness.limits import HarnessLimits
from math_tutor.harness.loop import HarnessBudgetExceeded, PedagogicalHarness
from math_tutor.harness.registry import PedagogicalToolRegistry, ToolRejected


class Service:
    def __init__(self, *, status=CommandStatus.APPLIED, answer_outcome=ObservationOutcome.CORRECT):
        self.status = status
        self.answer_outcome = answer_outcome
        self.commands = []
        self.cancelled = []

    def record_answer(self, command):
        self.commands.append(command)
        payload = RecordAnswerResult(AnswerCheck(AnswerOutcome.CORRECT, "exact-match"), self.answer_outcome)
        return CommandResult(command.command_id, self.status, "stale-session-version", payload)

    def commit_hint(self, command):
        self.commands.append(command)
        return CommandResult(command.command_id, self.status, "applied", CanonicalHintResult(command.hint_id, "Pista canónica."))

    def select_next_activity(self, command):
        self.commands.append(command)
        return CommandResult(command.command_id, self.status, "stale-session-version")

    def propose_profile_change(self, command):
        self.commands.append(command)
        return CommandResult(command.command_id, self.status, "applied")

    def stop_now(self, command):
        self.commands.append(command)
        self.cancelled.append(command.generation_id)
        return CommandResult(command.command_id, self.status, "persistence-unavailable")


class Model:
    def __init__(self, outputs): self.outputs, self.calls = list(outputs), []
    def complete(self, *, prompt, context, repair):
        self.calls.append(repair)
        return self.outputs.pop(0)


def context(*, confidence=.9, hint_text="texto falsificado", attempts=0, hints=0):
    return build_harness_context(
        session_id="s", learner_id="l", expected_session_version=1, expected_profile_version=1,
        generation_id="gen-1", authorised_objective_ids=("units-tens",), active_objective_ids=("units-tens",),
        activity=ActivityContext("a", "t", "units-tens", 2, "Pregunta", ("h1",), (hint_text,), hints, attempts),
        learner_state=LearnerState((("units-tens", "exploring"),), ("short-instructions",)),
        recent_history=(), current_turn=TurnEvidence("turn", "Cuatro", confidence), max_history_turns=2,
    )


def answer_tool():
    return {"type":"tool", "name":"record_answer", "arguments":{"turn_id":"turn", "answer":{"kind":"integer", "values":{"value":4}}}}


def test_low_confidence_uses_authoritative_observation_and_never_releases_correctness():
    service = Service(answer_outcome=ObservationOutcome.NOT_EVALUABLE)
    decision = PedagogicalToolRegistry(service, HarnessLimits()).execute(ToolProposal(ToolName.RECORD_ANSWER, answer_tool()["arguments"]), context(confidence=.2))
    assert decision.speech == "No estoy seguro de haber oído bien. ¿Puedes repetirlo?"
    assert "correct" not in decision.speech.casefold()


def test_hint_speech_comes_from_authoritative_service_result_not_context():
    decision = PedagogicalToolRegistry(Service(), HarnessLimits()).execute(ToolProposal(ToolName.GIVE_HINT, {}), context())
    assert decision.speech == "Pista canónica."
    assert "falsificado" not in decision.speech


def test_post_fence_rejection_or_bad_payload_never_gets_a_second_tool_attempt():
    service = Service(status=CommandStatus.REJECTED)
    model = Model([answer_tool(), {"type":"tool", "name":"give_hint", "arguments":{}}])
    with pytest.raises(ToolRejected, match="stale-session-version"):
        PedagogicalHarness(model, PedagogicalToolRegistry(service, HarnessLimits()), HarnessLimits()).run(context())
    assert len(model.calls) == 1
    assert len(service.commands) == 1


def test_pre_fence_tool_validation_can_use_the_single_repair_then_one_tool_step():
    model = Model([
        {"type":"tool", "name":"record_answer", "arguments":{"turn_id":"old", "answer":{"kind":"integer", "values":{"value":4}}}},
        answer_tool(),
    ])
    service = Service()
    decision = PedagogicalHarness(model, PedagogicalToolRegistry(service, HarnessLimits()), HarnessLimits()).run(context())
    assert decision.applied_tool is ToolName.RECORD_ANSWER
    assert model.calls == [False, True]
    assert len(service.commands) == 1


def test_budget_exhaustion_has_stable_error_and_preserves_cause():
    model = Model([{"bad": True}, {"still": "bad"}])
    with pytest.raises(HarnessBudgetExceeded, match="model-call-budget-exhausted") as caught:
        PedagogicalHarness(model, PedagogicalToolRegistry(Service(), HarnessLimits()), HarnessLimits()).run(context())
    assert isinstance(caught.value.__cause__, ValueError)


@pytest.mark.parametrize("output", [
    {"type":"tool", "name":"give_hint", "arguments":{}, "extra":"hidden"},
    {"type":"reply", "speech":"Vamos paso a paso.", "speech_kind":"social", "extra":"hidden"},
])
def test_model_output_contract_is_exhaustive(output):
    model = Model([output])
    with pytest.raises(HarnessBudgetExceeded) as caught:
        PedagogicalHarness(model, PedagogicalToolRegistry(Service(), HarnessLimits()), HarnessLimits(max_model_calls=1)).run(context())
    assert "invalid-" in str(caught.value.__cause__)


def test_stop_is_terminal_even_when_durable_stop_fails():
    service = Service(status=CommandStatus.PERSISTENCE_FAILED)
    decision = PedagogicalHarness(Model([]), PedagogicalToolRegistry(service, HarnessLimits()), HarnessLimits()).run(context(), stop_requested=True)
    assert decision.terminal
    assert decision.reason == "stop-persistence-pending"
    assert service.cancelled == ["gen-1"]
