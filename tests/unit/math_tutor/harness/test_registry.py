from dataclasses import replace

import pytest

from math_tutor.application.results import CommandResult, CommandStatus
from math_tutor.application.service import CanonicalHintResult, RecordAnswerResult
from math_tutor.application.regulation import ExecutedRegulationAction, RegulationResult
from math_tutor.application.provisioning import DEFAULT_REGULATION_POLICY
from math_tutor.application.service import CommitRegulation
from math_tutor.domain.regulation import PedagogicalStrategy
from math_tutor.domain.evidence import ObservationOutcome
from math_tutor.domain.activities import AnswerInputStatus
from math_tutor.domain.mathematics import AnswerCheck, AnswerOutcome
from math_tutor.domain.activities import Activity, StructuredAnswer
from math_tutor.domain.templates import ExpectedAnswerKind
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
    def select_next_activity(self, command):
        return self._apply(command, Activity(
            command.template_id, command.objective_id, command.difficulty,
            "Pregunta canónica nueva", {"number": 24},
            StructuredAnswer.evaluable(ExpectedAnswerKind.INTEGER, {"answer": 4}),
            (), (),
        ))
    def propose_profile_change(self, command): return self._apply(command, "proposal")
    def stop_now(self, command): return self._apply(command)
    def commit_regulation(self, command):
        return self._apply(command, RegulationResult(
            "Vamos paso a paso. ¿Cuántas?",
            ExecutedRegulationAction(command.strategy.value),
            command.expected_regulation_revision + 1,
        ))


@pytest.fixture
def context():
    return build_harness_context(
        session_id="s", learner_id="l", expected_session_version=1,
        expected_profile_version=1, generation_id="gen-1",
        authorised_objective_ids=("units-tens",), active_objective_ids=("units-tens",),
        activity=ActivityContext("a", "t", "units-tens", 2, "¿Cuántas?", ("h1", "h2"), ("Pista uno", "Pista dos"), 0),
        learner_state=LearnerState((("units-tens", "exploring"),), ("short-instructions",)),
        recent_history=(), current_turn=TurnEvidence("turn-1", "Cuatro", 0.9), max_history_turns=2,
        regulation_policy=DEFAULT_REGULATION_POLICY, regulation_revision=2,
        consecutive_regulation_turns=1,
    )


def test_tool_surface_is_intentionally_small():
    assert set(ToolName) == {
        ToolName.RECORD_ANSWER,
        ToolName.GIVE_HINT,
        ToolName.ADAPT_DIFFICULTY,
        ToolName.PROPOSE_SKILL_UPDATE,
        ToolName.END_SESSION,
        ToolName.REGULATE_CONVERSATION,
    }


def test_regulation_requires_exact_current_turn_and_authorised_compatible_strategy(context):
    registry = PedagogicalToolRegistry(CapturingService(), HarnessLimits())
    base = {"signal": "confused", "confidence": .7, "strategy": "simplify-language"}
    with pytest.raises(ToolRejected, match="current-turn-evidence"):
        registry.execute(ToolProposal(ToolName.REGULATE_CONVERSATION, {"turn_id": "old", **base}), context)
    with pytest.raises(ToolRejected, match="strategy-incompatible"):
        registry.execute(ToolProposal(ToolName.REGULATE_CONVERSATION, {
            "turn_id": "turn-1", "signal": "off-task", "confidence": .7,
            "strategy": "simplify-language",
        }), context)


def test_low_confidence_regulation_repeats_without_calling_service(context):
    service = CapturingService()
    result = PedagogicalToolRegistry(service, HarnessLimits()).execute(
        ToolProposal(ToolName.REGULATE_CONVERSATION, {
            "turn_id": "turn-1", "signal": "confused", "confidence": .49,
            "strategy": "simplify-language",
        }), context,
    )

    assert result.speech == context.activity.prompt_es
    assert result.reason == "low-regulation-confidence"
    assert service.commands == []


def test_valid_regulation_crosses_service_fence_and_releases_only_canonical_result(context):
    service = CapturingService()
    result = PedagogicalToolRegistry(service, HarnessLimits()).execute(
        ToolProposal(ToolName.REGULATE_CONVERSATION, {
            "turn_id": "turn-1", "signal": "confused", "confidence": .8,
            "strategy": "simplify-language",
        }), context,
    )

    assert isinstance(service.commands[-1], CommitRegulation)
    assert service.commands[-1].expected_regulation_revision == 2
    assert service.commands[-1].presentation == context.learner_state.presentation
    assert service.commands[-1].adaptations == context.adaptations
    assert result.speech == "Vamos paso a paso. ¿Cuántas?"
    assert result.reason == "regulated"


def test_regulation_observability_uses_closed_fields_without_child_text(context, caplog):
    caplog.set_level("INFO", logger="math_tutor.harness.registry")
    registry = PedagogicalToolRegistry(CapturingService(), HarnessLimits())

    registry.execute(ToolProposal(ToolName.REGULATE_CONVERSATION, {
        "turn_id": "turn-1", "signal": "confused", "confidence": .8,
        "strategy": "simplify-language",
    }), replace(context, current_turn=TurnEvidence(
        "turn-1", "secreto menor@example.com y dato privado", .9
    )))

    records = [record for record in caplog.records if record.name == "math_tutor.harness.registry"]
    assert [record.message for record in records] == [
        "conversation_regulation_proposed", "conversation_regulation_applied"
    ]
    assert records[0].signal == "confused"
    assert records[0].requested_strategy == "simplify-language"
    assert records[0].confidence_band == "high"
    assert records[0].consecutive_count == 1
    assert records[1].executed_action == "simplify-language"
    assert records[1].outcome == "unknown"
    assert records[1].regulation_revision == 3
    assert "menor@example.com" not in caplog.text
    assert "dato privado" not in caplog.text


def test_rejected_regulation_logs_only_closed_rejection_code(context, caplog):
    caplog.set_level("INFO", logger="math_tutor.harness.registry")
    policy = replace(
        context.regulation_policy,
        allowed_strategies=tuple(
            item for item in context.regulation_policy.allowed_strategies
            if item is not PedagogicalStrategy.SIMPLIFY_LANGUAGE
        ),
    )
    with pytest.raises(ToolRejected, match="strategy-not-authorised"):
        PedagogicalToolRegistry(CapturingService(), HarnessLimits()).execute(
            ToolProposal(ToolName.REGULATE_CONVERSATION, {
                "turn_id": "turn-1", "signal": "confused", "confidence": .8,
                "strategy": "simplify-language",
            }),
            replace(context, regulation_policy=policy),
        )

    assert [record.message for record in caplog.records] == [
        "conversation_regulation_proposed", "conversation_regulation_rejected"
    ]
    assert caplog.records[-1].rejection_code == "strategy-not-authorised"


@pytest.mark.parametrize(("arguments", "code"), [
    ({"turn_id":"old", "signal":"confused", "confidence":.8, "strategy":"simplify-language"}, "current-turn-evidence-required"),
    ({"turn_id":"turn-1", "signal":"unknown", "confidence":.8, "strategy":"simplify-language"}, "regulation-contract-invalid"),
    ({"turn_id":"turn-1", "signal":"confused", "confidence":float("nan"), "strategy":"simplify-language"}, "regulation-contract-invalid"),
    ({"turn_id":"turn-1", "signal":"confused", "confidence":.8, "strategy":"unknown"}, "regulation-contract-invalid"),
    ({"turn_id":"turn-1", "signal":"confused", "confidence":.8}, "tool-arguments-invalid"),
])
def test_every_early_regulation_rejection_emits_closed_code_without_arguments(context, caplog, arguments, code):
    caplog.set_level("INFO", logger="math_tutor.harness.registry")
    with pytest.raises(ToolRejected, match=code):
        PedagogicalToolRegistry(CapturingService(), HarnessLimits()).execute(
            ToolProposal(ToolName.REGULATE_CONVERSATION, arguments), context,
        )

    rejected=[record for record in caplog.records if record.message == "conversation_regulation_rejected"]
    assert len(rejected) == 1 and rejected[0].rejection_code == code
    assert str(arguments) not in caplog.text


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


def test_reliable_independent_answer_commits_an_evidence_selection(context):
    service = CapturingService()
    PedagogicalToolRegistry(service, HarnessLimits()).execute(
        ToolProposal(ToolName.RECORD_ANSWER, {"turn_id":"turn-1","answer":{"status":"evaluable","kind":"integer","values":{"answer":4}}}), context)
    command = service.commands[-1]
    assert command.retain_evidence is True
    assert command.evidence_id == "evidence-turn-1"
    assert command.reason_for_retention == "independent-answer"


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


def test_adaptation_releases_the_new_canonical_activity_prompt(context):
    result = PedagogicalToolRegistry(CapturingService(), HarnessLimits()).execute(
        ToolProposal(ToolName.ADAPT_DIFFICULTY, {
            "objective_id": "units-tens", "difficulty": 3,
            "seed": 7, "activity_id": "next",
        }),
        context,
    )

    assert result.speech == "Pregunta canónica nueva"


def test_skill_update_only_calls_proposal_service(context):
    service = CapturingService()
    result = PedagogicalToolRegistry(service, HarnessLimits()).execute(ToolProposal(ToolName.PROPOSE_SKILL_UPDATE, {"objective_id": "units-tens"}), context)
    assert type(service.commands[-1]).__name__ == "ProposeProfileChange"
    assert result.speech is None
