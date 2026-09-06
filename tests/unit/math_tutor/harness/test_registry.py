from dataclasses import replace

import pytest

from math_tutor.application.results import CommandResult, CommandStatus
from math_tutor.application.service import CanonicalHintResult, RecordAnswerResult
from math_tutor.domain.evidence import ObservationOutcome
from math_tutor.domain.activities import AnswerInputStatus
from math_tutor.domain.mathematics import AnswerCheck, AnswerOutcome
from math_tutor.harness.contracts import ToolName, ToolProposal
from math_tutor.harness.context import ActivityContext, LearnerState, TurnEvidence, build_harness_context
from math_tutor.harness.limits import HarnessLimits
from math_tutor.harness.registry import PedagogicalToolRegistry, ToolRejected


class CapturingService:
    def __init__(self):
        self.commands = []

    def _apply(self, command, payload=None):
        self.commands.append(command)
        return CommandResult(command.command_id, CommandStatus.APPLIED, "applied", payload)

    def record_answer(self, command):
        check = AnswerCheck(AnswerOutcome.CORRECT, "exact-match")
        return self._apply(command, RecordAnswerResult(check, ObservationOutcome.CORRECT))

    def commit_hint(self, command): return self._apply(command, CanonicalHintResult(command.hint_id, "Pista uno"))
    def select_next_activity(self, command): return self._apply(command)
    def propose_profile_change(self, command): return self._apply(command, "proposal")
    def stop_now(self, command): return self._apply(command)


@pytest.fixture
def context():
    return build_harness_context(
        session_id="s", learner_id="l", expected_session_version=1,
        expected_profile_version=1, generation_id="gen-1",
        authorised_objective_ids=("units-tens",), active_objective_ids=("units-tens",),
        activity=ActivityContext("a", "t", "units-tens", 2, "¿Cuántas?", ("h1", "h2"), ("Pista uno", "Pista dos"), 0),
        learner_state=LearnerState((("units-tens", "exploring"),), ("short-instructions",)),
        recent_history=(), current_turn=TurnEvidence("turn-1", "Cuatro", 0.9), max_history_turns=2,
    )


def test_tool_surface_is_intentionally_small():
    assert set(ToolName) == {ToolName.RECORD_ANSWER, ToolName.GIVE_HINT, ToolName.ADAPT_DIFFICULTY, ToolName.PROPOSE_SKILL_UPDATE, ToolName.END_SESSION}


def test_record_answer_requires_current_turn_evidence_and_structured_answer(context):
    registry = PedagogicalToolRegistry(CapturingService(), HarnessLimits())
    with pytest.raises(ToolRejected, match="current-turn-evidence"):
        registry.execute(ToolProposal(ToolName.RECORD_ANSWER, {"turn_id": "old", "answer": {"kind": "numeric", "values": {"value": 4}}}), context)
    with pytest.raises(ToolRejected, match="structured-answer"):
        registry.execute(ToolProposal(ToolName.RECORD_ANSWER, {"turn_id": "turn-1", "answer": "cuatro"}), context)


def test_record_answer_releases_only_deterministically_verified_speech(context):
    service = CapturingService()
    result = PedagogicalToolRegistry(service, HarnessLimits()).execute(
        ToolProposal(ToolName.RECORD_ANSWER, {
            "turn_id": "turn-1",
            "answer": {"status":"evaluable", "kind": "integer", "values": {"answer": 4}},
        }), context
    )
    command = service.commands[-1]
    assert command.response_text == context.current_turn.transcript
    assert command.stt_confidence == context.current_turn.stt_confidence
    assert result.speech == "Sí, esa respuesta es correcta."


def test_context_attempt_counter_cannot_short_circuit_authoritative_service(context):
    exhausted = replace(context, activity=replace(context.activity, attempts_used=3))
    service = CapturingService()
    PedagogicalToolRegistry(service, HarnessLimits(max_attempts_per_activity=3)).execute(
        ToolProposal(ToolName.RECORD_ANSWER, {"turn_id": "turn-1", "answer": {"status":"evaluable", "kind": "integer", "values": {"answer": 4}}}), exhausted)
    assert len(service.commands) == 1


def test_hint_is_next_reviewed_hint_and_cap_is_enforced(context):
    service = CapturingService()
    registry = PedagogicalToolRegistry(service, HarnessLimits(max_hints_per_activity=1))
    result = registry.execute(ToolProposal(ToolName.GIVE_HINT, {}), context)
    assert service.commands[-1].hint_id == "h1"
    assert result.speech == "Pista uno"
    with pytest.raises(ToolRejected, match="hint-cap"):
        registry.execute(ToolProposal(ToolName.GIVE_HINT, {}), replace(context, activity=replace(context.activity, hints_used=1)))

    with pytest.raises(ToolRejected, match="tool-arguments"):
        registry.execute(ToolProposal(ToolName.GIVE_HINT, {"hint": "inventada"}), context)


def test_adaptation_moves_one_step_and_stays_inside_active_objectives(context):
    registry = PedagogicalToolRegistry(CapturingService(), HarnessLimits())
    with pytest.raises(ToolRejected, match="difficulty-step"):
        registry.execute(ToolProposal(ToolName.ADAPT_DIFFICULTY, {"objective_id": "units-tens", "difficulty": 4, "seed": 1, "activity_id": "next"}), context)
    with pytest.raises(ToolRejected, match="objective-not-active"):
        registry.execute(ToolProposal(ToolName.ADAPT_DIFFICULTY, {"objective_id": "other", "difficulty": 3, "seed": 1, "activity_id": "next"}), context)

    with pytest.raises(ToolRejected, match="arguments-invalid"):
        registry.execute(ToolProposal(ToolName.ADAPT_DIFFICULTY, {"objective_id": "units-tens", "difficulty": 3, "seed": 1.5, "activity_id": "next"}), context)


def test_skill_update_only_calls_proposal_service(context):
    service = CapturingService()
    result = PedagogicalToolRegistry(service, HarnessLimits()).execute(ToolProposal(ToolName.PROPOSE_SKILL_UPDATE, {"objective_id": "units-tens"}), context)
    assert type(service.commands[-1]).__name__ == "ProposeProfileChange"
    assert result.speech is None
