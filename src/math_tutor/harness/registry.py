"""Authorization and mutation fence for pedagogical tools."""
from collections.abc import Mapping
from itertools import count
import logging
from typing import Protocol
from math_tutor.application.results import CommandStatus
from math_tutor.application.regulation import RegulationResult
from math_tutor.application.service import CanonicalHintResult, CommitHint, CommitRegulation, EndSession, ProposeProfileChange, RecordAnswer, RecordAnswerResult, SelectNextActivity
from math_tutor.domain.evidence import ObservationOutcome
from math_tutor.domain.activities import Activity, AnswerInputStatus, StructuredAnswer
from math_tutor.domain.mathematics import AnswerCheck, AnswerOutcome
from math_tutor.domain.templates import ExpectedAnswerKind
from math_tutor.domain.regulation import ConfidenceBand, ConversationalSignal, PedagogicalStrategy, compatible_strategies
from math_tutor.harness.context import HarnessContext
from math_tutor.harness.contracts import HarnessDecision, ToolName, ToolProposal
from math_tutor.harness.limits import HarnessLimits

logger = logging.getLogger(__name__)

class ToolRejected(ValueError):
    def __init__(self, reason: str, *, crossed_fence: bool = False) -> None:
        super().__init__(reason)
        self.crossed_fence = crossed_fence

class SessionCapExceeded(RuntimeError):
    pass

class TutoringMutationService(Protocol):
    def record_answer(self, command: RecordAnswer): ...
    def commit_hint(self, command: CommitHint): ...
    def select_next_activity(self, command: SelectNextActivity): ...
    def propose_profile_change(self, command: ProposeProfileChange): ...
    def stop_now(self, command: EndSession): ...
    def commit_regulation(self, command: CommitRegulation): ...

class PedagogicalToolRegistry:
    def __init__(self, service: TutoringMutationService, limits: HarnessLimits, guard=None) -> None:
        self._service, self._limits, self._sequence, self._guard = service, limits, count(1), guard
    def _base(self, context: HarnessContext, tool: ToolName) -> dict[str, object]:
        return dict(command_id=f"{context.current_turn.turn_id}:{tool.value}:{next(self._sequence)}", session_id=context.session_id, expected_session_version=context.expected_session_version, expected_profile_version=context.expected_profile_version, generation_id=context.generation_id)
    @staticmethod
    def _applied(result: object) -> None:
        if getattr(result, "status", None) is not CommandStatus.APPLIED:
            raise ToolRejected(getattr(result, "reason", "mutation-rejected"), crossed_fence=True)
    @staticmethod
    def _keys(arguments: object, *, required: set[str], optional: frozenset[str] = frozenset()) -> None:
        if not isinstance(arguments, Mapping): raise ToolRejected("tool-arguments-invalid")
        keys = set(arguments)
        if not required.issubset(keys) or keys - required - optional: raise ToolRejected("tool-arguments-invalid")
    def execute(self, proposal: ToolProposal, context: HarnessContext) -> HarnessDecision:
        if self._guard is not None and proposal.name is not ToolName.END_SESSION:
            reason = self._guard()
            if reason is not None:
                raise SessionCapExceeded(reason)
        args, name, base = proposal.arguments, proposal.name, self._base(context, proposal.name)
        if name is ToolName.RECORD_ANSWER:
            self._keys(args, required={"turn_id", "answer"})
            if args.get("turn_id") != context.current_turn.turn_id: raise ToolRejected("current-turn-evidence-required")
            raw = args.get("answer")
            if not isinstance(raw, dict) or not isinstance(raw.get("values"), dict): raise ToolRejected("structured-answer-required")
            try:
                if set(raw) != {"status", "kind", "values"}: raise ValueError
                status = AnswerInputStatus(raw["status"])
                if status is AnswerInputStatus.EVALUABLE:
                    kind = ExpectedAnswerKind(raw["kind"])
                    if kind is not context.activity.expected_answer_kind or set(raw["values"]) != set(context.activity.expected_answer_fields): raise ValueError
                else:
                    if raw["kind"] is not None or raw["values"] != {}: raise ValueError
                    kind = None
                answer = StructuredAnswer(kind=kind, values=raw["values"], status=status)
            except (KeyError, TypeError, ValueError): raise ToolRejected("structured-answer-invalid") from None
            retain = status is AnswerInputStatus.EVALUABLE and context.current_turn.stt_confidence >= 0.65 and context.activity.hints_used == 0
            command = RecordAnswer(**base, activity_id=context.activity.activity_id, answer=answer, response_text=context.current_turn.transcript, stt_confidence=context.current_turn.stt_confidence, assistance_level=context.activity.hints_used, observation_id=f"obs-{context.current_turn.turn_id}", evidence_id=f"evidence-{context.current_turn.turn_id}" if retain else None, retain_evidence=retain, reason_for_retention="independent-answer" if retain else None)
            result = self._service.record_answer(command); self._applied(result)
            if not isinstance(result.payload, RecordAnswerResult) or not isinstance(result.payload.mathematical_check, AnswerCheck):
                raise ToolRejected("verification-result-missing", crossed_fence=True)
            if result.payload.observation_outcome is ObservationOutcome.NOT_EVALUABLE:
                speech = "No estoy seguro de haber oído bien. ¿Puedes repetirlo?"
            else:
                speech = {AnswerOutcome.CORRECT:"Sí, esa respuesta es correcta.", AnswerOutcome.INCORRECT:"Esa respuesta todavía no es correcta. Vamos paso a paso.", AnswerOutcome.AMBIGUOUS:"No estoy seguro de haber entendido. ¿Puedes repetirlo?", AnswerOutcome.NOT_EVALUABLE:"No he podido comprobar la respuesta. ¿Puedes decirla de otra forma?"}[result.payload.mathematical_check.outcome]
            reason = "answer-correct" if result.payload.mathematical_check.outcome is AnswerOutcome.CORRECT else "accepted"
            return HarnessDecision(speech=speech, applied_tool=name, reason=reason, selected_evidence_id=f"evidence-{context.current_turn.turn_id}" if retain else None)
        if name is ToolName.GIVE_HINT:
            self._keys(args, required=set())
            index = context.activity.hints_used
            if index >= self._limits.max_hints_per_activity or index >= len(context.activity.hint_ids): raise ToolRejected("hint-cap-reached")
            result = self._service.commit_hint(CommitHint(**base, activity_id=context.activity.activity_id, hint_id=context.activity.hint_ids[index], hint_index=index)); self._applied(result)
            if not isinstance(result.payload, CanonicalHintResult) or result.payload.hint_id != context.activity.hint_ids[index] or not result.payload.speech:
                raise ToolRejected("canonical-hint-result-missing", crossed_fence=True)
            return HarnessDecision(speech=result.payload.speech, applied_tool=name)
        if name is ToolName.ADAPT_DIFFICULTY:
            self._keys(args, required={"objective_id", "difficulty", "seed", "activity_id"})
            objective, difficulty = args.get("objective_id"), args.get("difficulty")
            if objective not in context.active_objective_ids: raise ToolRejected("objective-not-active")
            if context.activities_used >= context.max_activities: raise ToolRejected("activity-cap-reached")
            if isinstance(difficulty, bool) or not isinstance(difficulty, int) or abs(difficulty-context.activity.difficulty) != 1: raise ToolRejected("difficulty-step-must-be-one")
            if not self._limits.min_difficulty <= difficulty <= self._limits.max_difficulty: raise ToolRejected("difficulty-out-of-range")
            activity_id, seed = args.get("activity_id"), args.get("seed")
            if not isinstance(activity_id, str) or not activity_id.strip() or isinstance(seed, bool) or not isinstance(seed, int): raise ToolRejected("adaptation-arguments-invalid")
            try: command = SelectNextActivity(**base, activity_id=activity_id, source_activity_id=context.activity.activity_id, objective_id=str(objective), template_id=context.activity.template_id, seed=seed, difficulty=difficulty)
            except (KeyError, TypeError, ValueError): raise ToolRejected("adaptation-arguments-invalid") from None
            result = self._service.select_next_activity(command); self._applied(result)
            if not isinstance(result.payload, Activity) or not result.payload.prompt_es:
                raise ToolRejected("canonical-activity-result-missing", crossed_fence=True)
            return HarnessDecision(speech=result.payload.prompt_es, applied_tool=name)
        if name is ToolName.PROPOSE_SKILL_UPDATE:
            self._keys(args, required={"objective_id"})
            objective = args.get("objective_id")
            if objective not in context.authorised_objective_ids: raise ToolRejected("objective-not-authorised")
            result = self._service.propose_profile_change(ProposeProfileChange(**base, objective_id=str(objective))); self._applied(result)
            return HarnessDecision(applied_tool=name, reason="proposal-only")
        if name is ToolName.REGULATE_CONVERSATION:
            try:
                self._keys(args, required={"turn_id", "signal", "confidence", "strategy"})
            except ToolRejected:
                logger.info("conversation_regulation_rejected", extra={"rejection_code": "tool-arguments-invalid"})
                raise
            if args.get("turn_id") != context.current_turn.turn_id:
                logger.info("conversation_regulation_rejected", extra={"rejection_code": "current-turn-evidence-required"})
                raise ToolRejected("current-turn-evidence-required")
            try:
                signal = ConversationalSignal(args.get("signal"))
                strategy = PedagogicalStrategy(args.get("strategy"))
                confidence_band = ConfidenceBand.from_confidence(args.get("confidence"))
            except (TypeError, ValueError):
                logger.info("conversation_regulation_rejected", extra={"rejection_code": "regulation-contract-invalid"})
                raise ToolRejected("regulation-contract-invalid") from None
            telemetry = {
                "signal": signal.value,
                "requested_strategy": strategy.value,
                "confidence_band": confidence_band.value,
                "consecutive_count": context.consecutive_regulation_turns,
            }
            logger.info("conversation_regulation_proposed", extra=telemetry)
            if confidence_band is ConfidenceBand.LOW:
                logger.info(
                    "conversation_regulation_rejected",
                    extra={**telemetry, "rejection_code": "low-regulation-confidence"},
                )
                return HarnessDecision(
                    speech=context.activity.prompt_es,
                    applied_tool=name,
                    reason="low-regulation-confidence",
                )
            if context.regulation_policy is None:
                logger.info("conversation_regulation_rejected", extra={**telemetry, "rejection_code": "regulation-policy-missing"})
                raise ToolRejected("regulation-policy-missing")
            if strategy not in context.regulation_policy.allowed_strategies:
                logger.info("conversation_regulation_rejected", extra={**telemetry, "rejection_code": "strategy-not-authorised"})
                raise ToolRejected("strategy-not-authorised")
            if strategy not in compatible_strategies(signal):
                logger.info("conversation_regulation_rejected", extra={**telemetry, "rejection_code": "strategy-incompatible"})
                raise ToolRejected("strategy-incompatible")
            result = self._service.commit_regulation(CommitRegulation(
                **base,
                turn_id=context.current_turn.turn_id,
                activity_id=context.activity.activity_id,
                expected_regulation_revision=context.regulation_revision,
                signal=signal,
                confidence_band=confidence_band,
                strategy=strategy,
                regulation_policy=context.regulation_policy,
                presentation=context.learner_state.presentation,
                adaptations=context.adaptations,
            ))
            if getattr(result, "status", None) is not CommandStatus.APPLIED:
                rejection = getattr(result, "reason", "mutation-rejected")
                logger.info("conversation_regulation_rejected", extra={**telemetry, "rejection_code": rejection})
                raise ToolRejected(rejection, crossed_fence=True)
            if (
                not isinstance(result.payload, RegulationResult)
                or not result.payload.speech
                or (
                    result.replayed
                    and not 0 <= result.payload.regulation_revision <= context.regulation_revision
                )
                or (
                    not result.replayed
                    and result.payload.regulation_revision != context.regulation_revision + 1
                )
            ):
                logger.info("conversation_regulation_rejected", extra={**telemetry, "rejection_code": "canonical-regulation-result-missing"})
                raise ToolRejected("canonical-regulation-result-missing", crossed_fence=True)
            logger.info(
                "conversation_regulation_applied",
                extra={
                    **telemetry,
                    "executed_action": result.payload.executed_action.value,
                    "outcome": "unknown",
                    "regulation_revision": result.payload.regulation_revision,
                },
            )
            return HarnessDecision(speech=result.payload.speech, applied_tool=name, reason="regulated")
        if name is ToolName.END_SESSION:
            self._keys(args, required=set(), optional={"reason"})
            reason = args.get("reason", "stop-requested")
            if not isinstance(reason, str) or not reason.strip(): raise ToolRejected("end-reason-invalid")
            result = self._service.stop_now(EndSession(**base, reason=reason))
            durable = getattr(result, "status", None) is CommandStatus.APPLIED
            return HarnessDecision(terminal=True, applied_tool=name, reason="accepted" if durable else "stop-persistence-pending")
        raise ToolRejected("unknown-tool")
