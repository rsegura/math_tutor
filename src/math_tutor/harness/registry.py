"""Authorization and mutation fence for pedagogical tools."""
from collections.abc import Mapping
from itertools import count
from typing import Protocol
from math_tutor.application.results import CommandStatus
from math_tutor.application.service import CommitHint, EndSession, ProposeProfileChange, RecordAnswer, SelectNextActivity
from math_tutor.domain.activities import AnswerInputStatus, StructuredAnswer
from math_tutor.domain.mathematics import AnswerCheck, AnswerOutcome
from math_tutor.domain.templates import ExpectedAnswerKind
from math_tutor.harness.context import HarnessContext
from math_tutor.harness.contracts import HarnessDecision, ToolName, ToolProposal
from math_tutor.harness.limits import HarnessLimits

class ToolRejected(ValueError): pass

class TutoringMutationService(Protocol):
    def record_answer(self, command: RecordAnswer): ...
    def commit_hint(self, command: CommitHint): ...
    def select_next_activity(self, command: SelectNextActivity): ...
    def propose_profile_change(self, command: ProposeProfileChange): ...
    def end_session(self, command: EndSession): ...

class PedagogicalToolRegistry:
    def __init__(self, service: TutoringMutationService, limits: HarnessLimits) -> None:
        self._service, self._limits, self._sequence = service, limits, count(1)
    def _base(self, context: HarnessContext, tool: ToolName) -> dict[str, object]:
        return dict(command_id=f"{context.current_turn.turn_id}:{tool.value}:{next(self._sequence)}", session_id=context.session_id, expected_session_version=context.expected_session_version, expected_profile_version=context.expected_profile_version, generation_id=context.generation_id)
    @staticmethod
    def _applied(result: object) -> None:
        if getattr(result, "status", None) is not CommandStatus.APPLIED: raise ToolRejected(getattr(result, "reason", "mutation-rejected"))
    @staticmethod
    def _keys(arguments: object, *, required: set[str], optional: frozenset[str] = frozenset()) -> None:
        if not isinstance(arguments, Mapping): raise ToolRejected("tool-arguments-invalid")
        keys = set(arguments)
        if not required.issubset(keys) or keys - required - optional: raise ToolRejected("tool-arguments-invalid")
    def execute(self, proposal: ToolProposal, context: HarnessContext) -> HarnessDecision:
        args, name, base = proposal.arguments, proposal.name, self._base(context, proposal.name)
        if name is ToolName.RECORD_ANSWER:
            self._keys(args, required={"turn_id", "answer"})
            if context.activity.attempts_used >= self._limits.max_attempts_per_activity: raise ToolRejected("attempt-cap-reached")
            if args.get("turn_id") != context.current_turn.turn_id: raise ToolRejected("current-turn-evidence-required")
            raw = args.get("answer")
            if not isinstance(raw, dict) or not isinstance(raw.get("values"), dict): raise ToolRejected("structured-answer-required")
            try:
                status = AnswerInputStatus(raw.get("status", "evaluable")); kind = ExpectedAnswerKind(raw["kind"]) if status is AnswerInputStatus.EVALUABLE else None
                answer = StructuredAnswer(kind=kind, values=raw["values"], status=status)
            except (KeyError, TypeError, ValueError): raise ToolRejected("structured-answer-invalid") from None
            command = RecordAnswer(**base, activity_id=context.activity.activity_id, answer=answer, response_text=context.current_turn.transcript, stt_confidence=context.current_turn.stt_confidence, assistance_level=context.activity.hints_used, observation_id=f"obs-{context.current_turn.turn_id}", evidence_id=None, retain_evidence=False, reason_for_retention=None)
            result = self._service.record_answer(command); self._applied(result)
            if not isinstance(result.payload, AnswerCheck): raise ToolRejected("verification-result-missing")
            speech = {AnswerOutcome.CORRECT:"Sí, esa respuesta es correcta.", AnswerOutcome.INCORRECT:"Esa respuesta todavía no es correcta. Vamos paso a paso.", AnswerOutcome.AMBIGUOUS:"No estoy seguro de haber entendido. ¿Puedes repetirlo?", AnswerOutcome.NOT_EVALUABLE:"No he podido comprobar la respuesta. ¿Puedes decirla de otra forma?"}[result.payload.outcome]
            return HarnessDecision(speech=speech, applied_tool=name)
        if name is ToolName.GIVE_HINT:
            self._keys(args, required=set())
            index = context.activity.hints_used
            if index >= self._limits.max_hints_per_activity or index >= len(context.activity.hint_ids): raise ToolRejected("hint-cap-reached")
            result = self._service.commit_hint(CommitHint(**base, activity_id=context.activity.activity_id, hint_id=context.activity.hint_ids[index], hint_index=index)); self._applied(result)
            return HarnessDecision(speech=context.activity.hint_texts[index], applied_tool=name)
        if name is ToolName.ADAPT_DIFFICULTY:
            self._keys(args, required={"objective_id", "difficulty", "seed", "activity_id"})
            objective, difficulty = args.get("objective_id"), args.get("difficulty")
            if objective not in context.active_objective_ids: raise ToolRejected("objective-not-active")
            if isinstance(difficulty, bool) or not isinstance(difficulty, int) or abs(difficulty-context.activity.difficulty) != 1: raise ToolRejected("difficulty-step-must-be-one")
            if not self._limits.min_difficulty <= difficulty <= self._limits.max_difficulty: raise ToolRejected("difficulty-out-of-range")
            activity_id, seed = args.get("activity_id"), args.get("seed")
            if not isinstance(activity_id, str) or not activity_id.strip() or isinstance(seed, bool) or not isinstance(seed, int): raise ToolRejected("adaptation-arguments-invalid")
            try: command = SelectNextActivity(**base, activity_id=activity_id, objective_id=str(objective), template_id=context.activity.template_id, seed=seed, difficulty=difficulty)
            except (KeyError, TypeError, ValueError): raise ToolRejected("adaptation-arguments-invalid") from None
            result = self._service.select_next_activity(command); self._applied(result)
            return HarnessDecision(applied_tool=name)
        if name is ToolName.PROPOSE_SKILL_UPDATE:
            self._keys(args, required={"objective_id"})
            objective = args.get("objective_id")
            if objective not in context.authorised_objective_ids: raise ToolRejected("objective-not-authorised")
            result = self._service.propose_profile_change(ProposeProfileChange(**base, objective_id=str(objective))); self._applied(result)
            return HarnessDecision(applied_tool=name, reason="proposal-only")
        if name is ToolName.END_SESSION:
            self._keys(args, required=set(), optional={"reason"})
            reason = args.get("reason", "stop-requested")
            if not isinstance(reason, str) or not reason.strip(): raise ToolRejected("end-reason-invalid")
            result = self._service.end_session(EndSession(**base, reason=reason)); self._applied(result)
            return HarnessDecision(terminal=True, applied_tool=name)
        raise ToolRejected("unknown-tool")
