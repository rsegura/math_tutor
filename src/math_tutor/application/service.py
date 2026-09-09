"""Validated mutation fence for tutoring commands."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum
from hashlib import sha256
import json

from math_tutor.application.ports import (
    CommitOutcome,
    ActivityProgress,
    ActivityProgressExpectation,
    MutationBatch,
    PendingRegulationEvent,
    RegulationMutation,
    RegulationOutcome,
    LearnerSupportReceipt,
    ObservationExpectation,
    StoredActivity,
    TutoringEvent,
    TutoringRepository,
)
from math_tutor.application.results import CommandResult, CommandStatus
from math_tutor.application.regulation import (
    CanonicalHintTextMissing,
    NoAuthorisedRegulationFallback,
    RegulationResult,
    plan_regulation_response,
    select_next_reviewed_hint,
)
from math_tutor.application.session_runtime import SessionRuntime
from math_tutor.domain.activities import InvalidActivity, StructuredAnswer, generate_activity
from math_tutor.domain.evidence import (
    EvidenceRecord,
    Observation,
    TranscriptionReliabilityPolicy,
    ObservationOutcome,
)
from math_tutor.domain.learning import ProgressionPolicy
from math_tutor.domain.mathematics import AnswerCheck, verify_answer
from math_tutor.domain.templates import ActivityTemplateCatalog, InvalidActivityTemplate
from math_tutor.domain.regulation import (
    ConfidenceBand,
    ConversationalSignal,
    ExecutedRegulationAction,
    PedagogicalStrategy,
    RegulationPolicy,
    compatible_strategies,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class Command:
    command_id: str
    session_id: str
    expected_session_version: int
    expected_profile_version: int
    generation_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordAnswer(Command):
    activity_id: str
    answer: StructuredAnswer
    response_text: str | None
    stt_confidence: float
    assistance_level: int
    observation_id: str
    evidence_id: str | None
    retain_evidence: bool
    reason_for_retention: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class CommitHint(Command):
    activity_id: str
    hint_id: str
    hint_index: int


@dataclass(frozen=True, slots=True, kw_only=True)
class SupportLearner(Command):
    turn_id: str
    activity_id: str
    regulation_policy: RegulationPolicy
    presentation: tuple[str, ...]
    adaptations: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class CommitRegulation(Command):
    turn_id: str
    activity_id: str
    expected_regulation_revision: int
    signal: ConversationalSignal
    confidence_band: ConfidenceBand
    strategy: PedagogicalStrategy
    regulation_policy: RegulationPolicy
    presentation: tuple[str, ...]
    adaptations: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class SelectNextActivity(Command):
    activity_id: str
    objective_id: str
    template_id: str
    seed: int
    difficulty: int
    source_activity_id: str | None = None


@dataclass(frozen=True, slots=True)
class PedagogicalMutationPolicy:
    max_attempts_per_activity: int = 3
    max_hints_per_activity: int = 3
    min_repeated_outcomes_for_adaptation: int = 2

    def __post_init__(self) -> None:
        for name in ("max_attempts_per_activity", "max_hints_per_activity", "min_repeated_outcomes_for_adaptation"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class RecordAnswerResult:
    mathematical_check: AnswerCheck
    observation_outcome: ObservationOutcome


@dataclass(frozen=True, slots=True)
class CanonicalHintResult:
    hint_id: str
    speech: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ProposeEvidence(Command):
    observation_id: str
    evidence_id: str
    interpretation: str
    reason_for_retention: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ProposeProfileChange(Command):
    objective_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class EndSession(Command):
    reason: str


class TutoringService:
    def __init__(
        self,
        repository: TutoringRepository,
        runtime: SessionRuntime,
        template_catalog: ActivityTemplateCatalog,
        transcription_policy: TranscriptionReliabilityPolicy,
        progression_policy: ProgressionPolicy,
        pedagogical_policy: PedagogicalMutationPolicy | None = None,
        reviewed_hint_texts: Mapping[str, str] | None = None,
    ) -> None:
        if not isinstance(template_catalog, ActivityTemplateCatalog):
            raise TypeError("template_catalog must be an ActivityTemplateCatalog")
        if not isinstance(transcription_policy, TranscriptionReliabilityPolicy):
            raise TypeError("transcription_policy must be a TranscriptionReliabilityPolicy")
        if not isinstance(progression_policy, ProgressionPolicy):
            raise TypeError("progression_policy must be a ProgressionPolicy")
        self._repository = repository
        self._runtime = runtime
        self._template_catalog = template_catalog
        self._transcription_policy = transcription_policy
        self._progression_policy = progression_policy
        self._pedagogical_policy = pedagogical_policy or PedagogicalMutationPolicy()
        self._reviewed_hint_texts = dict(reviewed_hint_texts or {})

    @staticmethod
    def _progress(state, activity_id: str, difficulty: int) -> tuple[ActivityProgress, bool]:
        stored = state.progress_for(activity_id)
        return (stored, True) if stored is not None else (
            ActivityProgress(activity_id, 0, 0, difficulty), False
        )

    @staticmethod
    def _progress_changes(progress: ActivityProgress, existed: bool, **changes):
        updated = replace(progress, version=progress.version + 1 if existed else 1, **changes)
        expected = (ActivityProgressExpectation(progress.activity_id, progress.version),) if existed else ()
        return updated, expected

    @staticmethod
    def _canonical(value: object) -> object:
        """Convert supported command values to a stable, versionable JSON tree."""

        if is_dataclass(value) and not isinstance(value, type):
            return {
                "__type__": f"{type(value).__module__}.{type(value).__qualname__}",
                "fields": {
                    field: TutoringService._canonical(item)
                    for field, item in sorted(
                        (definition.name, getattr(value, definition.name))
                        for definition in fields(value)
                    )
                },
            }
        if isinstance(value, Enum):
            return {
                "__enum__": f"{type(value).__module__}.{type(value).__qualname__}",
                "value": value.value,
            }
        if isinstance(value, Mapping):
            entries = [
                (TutoringService._canonical(key), TutoringService._canonical(item))
                for key, item in value.items()
            ]
            entries.sort(key=lambda pair: json.dumps(pair[0], sort_keys=True, separators=(",", ":")))
            return {"__mapping__": entries}
        if isinstance(value, (tuple, list)):
            return [TutoringService._canonical(item) for item in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise TypeError(f"unsupported fingerprint value: {type(value).__qualname__}")

    @staticmethod
    def _fingerprint(command: Command) -> str:
        document = {"serialization_version": 1, "command": TutoringService._canonical(command)}
        encoded = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(encoded).hexdigest()

    def _state(self, command: Command):
        try:
            prior = self._repository.load_command_result(command.command_id)
            if prior is not None:
                if prior.command_fingerprint != self._fingerprint(command):
                    return None, self._rejected(command, "command-id-collision")
                return None, prior.result.as_replay()
            if not self._runtime.is_active(command.session_id, command.generation_id):
                return None, self._rejected(command, "generation-not-active")
            state = self._repository.load_state(command.session_id)
        except Exception:
            return None, self._failed(command, "persistence-unavailable")
        if state is None:
            return None, self._rejected(command, "session-not-found")
        if state.session.ended:
            return None, self._rejected(command, "session-ended")
        if command.expected_session_version != state.session.version:
            return None, self._rejected(command, "stale-session-version")
        if command.expected_profile_version != state.profile_version:
            return None, self._rejected(command, "stale-profile-version")
        return state, None

    def _prior_command(self, command: Command) -> CommandResult | None:
        """Preserve command collision semantics before turn-scoped replay."""
        try:
            prior = self._repository.load_command_result(command.command_id)
        except Exception:
            return self._failed(command, "persistence-unavailable")
        if prior is None:
            return None
        if prior.command_fingerprint != self._fingerprint(command):
            return self._rejected(command, "command-id-collision")
        return prior.result.as_replay()

    @staticmethod
    def _receipt_action(receipt: LearnerSupportReceipt) -> ExecutedRegulationAction:
        aliases = {
            "hint": ExecutedRegulationAction.GIVE_ORDERED_HINT,
            "repeat": ExecutedRegulationAction.REPEAT_INSTRUCTION,
        }
        if receipt.action in aliases:
            return aliases[receipt.action]
        return ExecutedRegulationAction(receipt.action)

    @staticmethod
    def _regulation_semantic_fingerprint(command: CommitRegulation) -> str:
        semantic = {
            "serialization_version": 1,
            "session_id": command.session_id,
            "turn_id": command.turn_id,
            "activity_id": command.activity_id,
            "expected_session_version": command.expected_session_version,
            "expected_profile_version": command.expected_profile_version,
            "expected_regulation_revision": command.expected_regulation_revision,
            "signal": command.signal,
            "confidence_band": command.confidence_band,
            "requested_strategy": command.strategy,
            "regulation_policy": command.regulation_policy,
            "presentation": command.presentation,
            "adaptations": command.adaptations,
        }
        encoded = json.dumps(
            TutoringService._canonical(semantic), ensure_ascii=False,
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return sha256(encoded).hexdigest()

    def _regulation_receipt_result(
        self, command: CommitRegulation, receipt: LearnerSupportReceipt
    ) -> CommandResult | None:
        if (
            receipt.activity_id != command.activity_id
            or receipt.regulation_revision != command.expected_regulation_revision + 1
            or receipt.decision_reason != "regulated"
            or receipt.semantic_fingerprint
            != self._regulation_semantic_fingerprint(command)
        ):
            return None
        return CommandResult(
            command.command_id, CommandStatus.APPLIED, "replayed",
            RegulationResult(
                receipt.speech, self._receipt_action(receipt),
                receipt.regulation_revision,
            ),
            replayed=True,
        )

    def _load_turn_receipt(
        self, command: Command
    ) -> tuple[LearnerSupportReceipt | None, CommandResult | None]:
        prior = self._prior_command(command)
        if prior is not None:
            return None, prior
        try:
            return self._repository.load_support_receipt(
                command.session_id, command.turn_id
            ), None
        except Exception:
            return None, self._failed(command, "persistence-unavailable")

    def _regulation_result_or_race_winner(
        self, command: CommitRegulation, result: CommandResult
    ) -> CommandResult:
        if result.status is CommandStatus.APPLIED:
            return result
        for _ in range(8):
            try:
                receipt = self._repository.load_support_receipt(
                    command.session_id, command.turn_id
                )
            except Exception:
                return result
            if receipt is None:
                continue
            winner = self._regulation_receipt_result(command, receipt)
            return winner if winner is not None else result
        return result

    @staticmethod
    def _rejected(command: Command, reason: str) -> CommandResult:
        return CommandResult(command.command_id, CommandStatus.REJECTED, reason)

    @staticmethod
    def _failed(command: Command, reason: str) -> CommandResult:
        return CommandResult(command.command_id, CommandStatus.PERSISTENCE_FAILED, reason)

    def _commit(self, command: Command, **changes) -> CommandResult:
        if not self._runtime.consume_generation(
            command.session_id, command.generation_id
        ):
            return self._rejected(command, "generation-not-active")
        try:
            result = CommandResult(
                command.command_id,
                CommandStatus.APPLIED,
                "applied",
                changes.pop("payload", None),
            )
            batch = MutationBatch(
                command_id=command.command_id,
                command_fingerprint=self._fingerprint(command),
                session_id=command.session_id,
                expected_session_version=command.expected_session_version,
                expected_profile_version=command.expected_profile_version,
                result=result,
                **changes,
            )
            try:
                decision = self._repository.commit_once(batch)
            except Exception:
                return self._failed(command, "persistence-unavailable")
            if decision.outcome is CommitOutcome.COLLISION:
                return self._rejected(command, "command-id-collision")
            if decision.outcome is CommitOutcome.REPLAYED:
                if decision.result is None:
                    return self._failed(command, "invalid-persistence-response")
                if decision.stored_fingerprint != batch.command_fingerprint:
                    return self._rejected(command, "command-id-collision")
                return decision.result.as_replay()
            if decision.outcome is CommitOutcome.CONFLICT:
                return self._rejected(command, decision.reason or "concurrent-mutation")
            if (
                decision.outcome is not CommitOutcome.APPLIED
                or decision.result is None
                or decision.stored_fingerprint != batch.command_fingerprint
            ):
                return self._failed(command, "invalid-persistence-response")
            return decision.result
        finally:
            self._runtime.complete_mutation(command.session_id)

    def record_answer(self, command: RecordAnswer) -> CommandResult:
        state, error = self._state(command)
        if error: return error
        try:
            activity = self._repository.load_activity(command.session_id, command.activity_id)
        except Exception:
            return self._failed(command, "persistence-unavailable")
        if activity is None: return self._rejected(command, "activity-not-found")
        if activity.objective_id not in state.session.authorised_objective_ids:
            return self._rejected(command, "objective-not-authorised")
        progress, existed = self._progress(state, command.activity_id, activity.difficulty)
        check = verify_answer(activity, command.answer)
        try:
            observation = Observation.from_answer(
                observation_id=command.observation_id,
                learner_id=state.session.learner_id,
                session_id=command.session_id,
                objective_id=activity.objective_id,
                activity_id=command.activity_id,
                answer_outcome=check.outcome,
                stt_confidence=command.stt_confidence,
                assistance_level=progress.hints_used,
                response_text=command.response_text,
                transcription_policy=self._transcription_policy,
            )
        except (TypeError, ValueError):
            return self._rejected(command, "domain-validation-error")
        evidence = ()
        if command.retain_evidence:
            if command.evidence_id is None or command.reason_for_retention is None:
                return self._rejected(command, "retained-evidence-requires-id-and-reason")
            try:
                evidence = (EvidenceRecord.initial(
                    evidence_id=command.evidence_id,
                    learner_id=state.session.learner_id,
                    observation=observation,
                    reason_for_retention=command.reason_for_retention,
                ),)
            except (TypeError, ValueError):
                return self._rejected(command, "domain-validation-error")
        correct = observation.outcome is ObservationOutcome.CORRECT
        incorrect = observation.outcome is ObservationOutcome.INCORRECT
        evaluable = correct or incorrect
        if evaluable and progress.attempts_used >= self._pedagogical_policy.max_attempts_per_activity:
            return self._rejected(command, "attempt-cap-reached")
        progress, expected_progress = self._progress_changes(
            progress, existed,
            attempts_used=progress.attempts_used + 1 if evaluable else progress.attempts_used,
            consecutive_correct=(
                progress.consecutive_correct + 1 if correct
                else 0 if incorrect
                else progress.consecutive_correct
            ),
            consecutive_incorrect=(
                progress.consecutive_incorrect + 1 if incorrect
                else 0 if correct
                else progress.consecutive_incorrect
            ),
        )
        session = replace(state.session, version=state.session.version + 1)
        regulation_changes = {}
        if evaluable:
            regulation_changes = dict(
                expected_regulation_revision=state.regulation_revision,
                regulation_mutation=RegulationMutation(
                    state.regulation_revision + 1, 0, state.activity_sequence,
                    state.pending_regulation_event.strategy if state.pending_regulation_event else ExecutedRegulationAction.CAP_CHOICE,
                    close_outcome=RegulationOutcome.ANSWERED,
                ),
            )
        return self._commit(command, session=session, observations=(observation,), evidence=evidence,
            events=(TutoringEvent("answer-recorded", command.session_id, activity.objective_id, command.activity_id, check.reason),),
            activity_progress=(progress,), expected_activity_progress=expected_progress,
            payload=RecordAnswerResult(check, observation.outcome), **regulation_changes)

    def commit_hint(self, command: CommitHint) -> CommandResult:
        state, error = self._state(command)
        if error: return error
        try: activity = self._repository.load_activity(command.session_id, command.activity_id)
        except Exception: return self._failed(command, "persistence-unavailable")
        if activity is None: return self._rejected(command, "activity-not-found")
        if activity.objective_id not in state.session.authorised_objective_ids:
            return self._rejected(command, "objective-not-authorised")
        progress, existed = self._progress(state, command.activity_id, activity.difficulty)
        if command.hint_index != progress.hints_used:
            return self._rejected(command, "hint-not-next")
        try:
            selection = select_next_reviewed_hint(
                activity, progress,
                max_hints=self._pedagogical_policy.max_hints_per_activity,
                reviewed_hint_texts=self._reviewed_hint_texts,
            )
        except CanonicalHintTextMissing:
            return self._rejected(command, "canonical-hint-text-missing")
        if selection is None:
            return self._rejected(command, "hint-cap-reached")
        if command.hint_index < 0 or selection.hint_id != command.hint_id:
            return self._rejected(command, "hint-not-reviewed-at-index")
        progress, expected_progress = self._progress_changes(
            progress, existed, hints_used=progress.hints_used + 1
        )
        session = replace(state.session, version=state.session.version + 1)
        return self._commit(command, session=session,
            activity_progress=(progress,), expected_activity_progress=expected_progress,
            events=(TutoringEvent("hint-committed", command.session_id, activity.objective_id, command.activity_id, command.hint_id),),
            payload=CanonicalHintResult(command.hint_id, selection.speech))

    def commit_regulation(self, command: CommitRegulation) -> CommandResult:
        """Execute one validated strategy and return speech only after the fence."""

        receipt, replay = self._load_turn_receipt(command)
        if replay is not None:
            if replay.reason != "command-id-collision":
                return replay
            try:
                receipt = self._repository.load_support_receipt(
                    command.session_id, command.turn_id
                )
            except Exception:
                return replay
        if receipt is not None:
            winner = self._regulation_receipt_result(command, receipt)
            if winner is not None:
                return winner
            return replay or self._rejected(command, "command-id-collision")

        state, error = self._state(command)
        if error:
            return error
        if command.expected_regulation_revision != state.regulation_revision:
            return self._rejected(command, "stale-regulation-revision")
        if (
            not isinstance(command.signal, ConversationalSignal)
            or not isinstance(command.confidence_band, ConfidenceBand)
            or not isinstance(command.strategy, PedagogicalStrategy)
            or command.strategy not in compatible_strategies(command.signal)
        ):
            return self._rejected(command, "invalid-regulation-command")
        if not isinstance(command.regulation_policy, RegulationPolicy):
            return self._rejected(command, "invalid-regulation-command")
        try:
            activity = self._repository.load_activity(command.session_id, command.activity_id)
        except Exception:
            return self._failed(command, "persistence-unavailable")
        if activity is None:
            return self._rejected(command, "activity-not-found")
        if activity.objective_id not in state.session.authorised_objective_ids:
            return self._rejected(command, "objective-not-authorised")

        progress, existed = self._progress(state, command.activity_id, activity.difficulty)
        progress_changes = ()
        expected_progress = ()
        events = ()
        try:
            plan = plan_regulation_response(
                signal=command.signal, requested_strategy=command.strategy,
                policy=command.regulation_policy,
                consecutive_turns=state.consecutive_regulation_turns,
                activity=activity, progress=progress,
                max_hints=self._pedagogical_policy.max_hints_per_activity,
                reviewed_hint_texts=self._reviewed_hint_texts,
                presentation=command.presentation, adaptations=command.adaptations,
            )
        except NoAuthorisedRegulationFallback as error:
            return self._rejected(command, str(error))
        except ValueError:
            return self._rejected(command, "invalid-regulation-presentation")
        if plan.hint is not None:
            progress, expected_progress = self._progress_changes(
                progress, existed, hints_used=progress.hints_used + 1
            )
            progress_changes = (progress,)
            events = (TutoringEvent(
                "hint-committed", command.session_id, activity.objective_id,
                command.activity_id, plan.hint.hint_id,
            ),)
        executed_action = plan.executed_action
        speech = plan.speech
        next_count = (
            command.regulation_policy.max_consecutive_regulation_turns
            if executed_action is ExecutedRegulationAction.CAP_CHOICE
            else state.consecutive_regulation_turns + 1
        )

        revision = state.regulation_revision + 1
        pending = PendingRegulationEvent(
            event_id=f"regulation-{command.session_id}-{command.turn_id}",
            turn_id=command.turn_id,
            activity_id=command.activity_id,
            signal=command.signal,
            confidence_band=command.confidence_band,
            strategy=executed_action,
            ordinal=state.activity_sequence + 1,
        )
        payload = RegulationResult(speech, executed_action, revision)
        receipt = LearnerSupportReceipt(
            command.session_id, command.turn_id, command.activity_id,
            executed_action.value, speech, revision, "regulated",
            self._regulation_semantic_fingerprint(command),
        )
        result = self._commit(
            command,
            activity_progress=progress_changes,
            expected_activity_progress=expected_progress,
            events=events,
            expected_regulation_revision=state.regulation_revision,
            regulation_mutation=RegulationMutation(
                revision, next_count, state.activity_sequence + 1, executed_action,
                pending_event=pending,
                close_outcome=(RegulationOutcome.REPEATED_DIFFICULTY
                               if state.pending_regulation_event else None),
            ),
            support_receipts=(receipt,),
            payload=payload,
        )
        return self._regulation_result_or_race_winner(command, result)

    def support_learner(self, command: SupportLearner) -> CommandResult:
        """Persist and replay deterministic help without storing learner text."""
        receipt, replay = self._load_turn_receipt(command)
        if replay is not None:
            return replay
        if receipt is not None:
            return CommandResult(
                command.command_id, CommandStatus.APPLIED, "replayed", receipt,
                replayed=True,
            )
        state, error = self._state(command)
        if error:
            return self._support_result_or_race_winner(command, error)
        try:
            activity = self._repository.load_activity(command.session_id, command.activity_id)
        except Exception:
            return self._failed(command, "persistence-unavailable")
        if activity is None:
            return self._rejected(command, "activity-not-found")
        if activity.objective_id not in state.session.authorised_objective_ids:
            return self._rejected(command, "objective-not-authorised")
        if not isinstance(command.regulation_policy, RegulationPolicy):
            return self._rejected(command, "invalid-regulation-command")
        progress, existed = self._progress(state, command.activity_id, activity.difficulty)
        cap = command.regulation_policy.max_consecutive_regulation_turns
        try:
            plan = plan_regulation_response(
                signal=ConversationalSignal.REQUESTING_HELP,
                requested_strategy=None,
                policy=command.regulation_policy,
                consecutive_turns=state.consecutive_regulation_turns,
                activity=activity, progress=progress,
                max_hints=self._pedagogical_policy.max_hints_per_activity,
                reviewed_hint_texts=self._reviewed_hint_texts,
                presentation=command.presentation, adaptations=command.adaptations,
            )
        except NoAuthorisedRegulationFallback as error:
            return self._rejected(command, str(error))
        except ValueError:
            return self._rejected(command, "invalid-regulation-presentation")
        action, speech = plan.executed_action, plan.speech
        if plan.hint is not None:
            progress, expected_progress = self._progress_changes(
                progress, existed, hints_used=progress.hints_used + 1
            )
            session = replace(state.session, version=state.session.version + 1)
            receipt_action = "hint"
            progress_changes = (progress,)
            events = (TutoringEvent("hint-committed", command.session_id,
                                    activity.objective_id, command.activity_id,
                                    plan.hint.hint_id),)
        else:
            receipt_action = (
                "repeat" if action is ExecutedRegulationAction.REPEAT_INSTRUCTION
                else action.value
            )
            expected_progress = progress_changes = events = ()
            session = None
        receipt = LearnerSupportReceipt(
            command.session_id, command.turn_id, command.activity_id,
            receipt_action, speech, state.regulation_revision + 1,
            f"learner-support-{receipt_action}",
        )
        next_count = min(
            cap, state.consecutive_regulation_turns
            + (0 if action is ExecutedRegulationAction.CAP_CHOICE else 1),
        )
        pending = PendingRegulationEvent(
            f"regulation-{command.session_id}-{command.turn_id}", command.turn_id,
            command.activity_id, ConversationalSignal.REQUESTING_HELP,
            ConfidenceBand.HIGH, action, state.activity_sequence + 1,
        )
        result = self._commit(
            command, session=session, activity_progress=progress_changes,
            expected_activity_progress=expected_progress, events=events,
            support_receipts=(receipt,),
            payload=receipt,
            expected_regulation_revision=state.regulation_revision,
            regulation_mutation=RegulationMutation(
                state.regulation_revision + 1, next_count,
                state.activity_sequence + 1, action, pending,
                RegulationOutcome.REPEATED_DIFFICULTY if state.pending_regulation_event else None,
            ),
        )
        return self._support_result_or_race_winner(command, result)

    def _support_result_or_race_winner(
        self, command: SupportLearner, result: CommandResult
    ) -> CommandResult:
        if result.status is CommandStatus.APPLIED:
            return result
        try:
            winner = self._repository.load_support_receipt(
                command.session_id, command.turn_id
            )
        except Exception:
            return result
        if winner is None:
            return result
        return CommandResult(
            command.command_id, CommandStatus.APPLIED, "replayed", winner, replayed=True
        )

    def select_next_activity(self, command: SelectNextActivity) -> CommandResult:
        state, error = self._state(command)
        if error: return error
        if command.objective_id not in state.session.authorised_objective_ids:
            return self._rejected(command, "objective-not-authorised")
        if command.objective_id not in state.session.active_objective_ids:
            return self._rejected(command, "objective-not-active")
        try:
            template = self._template_catalog.template(command.template_id)
        except InvalidActivityTemplate:
            return self._rejected(command, "template-not-found")
        if template.objective_id != command.objective_id:
            return self._rejected(command, "template-objective-mismatch")
        try:
            existing_activity = self._repository.load_activity(
                command.session_id, command.activity_id
            )
        except Exception:
            return self._failed(command, "persistence-unavailable")
        if existing_activity is not None:
            return self._rejected(command, "activity-id-already-exists")
        source_id = command.source_activity_id
        if source_id is not None:
            try: source_activity = self._repository.load_activity(command.session_id, source_id)
            except Exception: return self._failed(command, "persistence-unavailable")
            if source_activity is None: return self._rejected(command, "source-activity-not-found")
            if source_activity.objective_id != command.objective_id:
                return self._rejected(command, "source-objective-mismatch")
            if source_activity.template_id != command.template_id:
                return self._rejected(command, "source-template-mismatch")
            progress, existed = self._progress(state, source_id, source_activity.difficulty)
            difficulty_step = command.difficulty - progress.difficulty
            if difficulty_step not in (-1, 0, 1):
                return self._rejected(command, "difficulty-step-must-be-one")
            if difficulty_step == 0:
                if progress.consecutive_correct < 1:
                    return self._rejected(command, "correct-answer-required")
                reason = "correct-answer"
            else:
                repeated = progress.consecutive_correct if difficulty_step > 0 else progress.consecutive_incorrect
                if repeated < self._pedagogical_policy.min_repeated_outcomes_for_adaptation:
                    return self._rejected(command, "insufficient-repeated-evidence")
                reason = "repeated-correct" if difficulty_step > 0 else "repeated-incorrect"
            consumed_progress, expected_progress = self._progress_changes(
                progress,
                existed,
                consecutive_correct=0,
                consecutive_incorrect=0,
            )
        else:
            consumed_progress, expected_progress, reason = None, (), "initial-selection"
        try:
            activity = generate_activity(template, seed=command.seed, difficulty=command.difficulty)
        except (InvalidActivity, TypeError, ValueError):
            return self._rejected(command, "activity-generation-invalid")
        session = replace(state.session, version=state.session.version + 1)
        next_progress = ActivityProgress(
            command.activity_id, 0, 0, activity.difficulty,
            version=1,
        )
        progress_changes = (
            (consumed_progress, next_progress)
            if consumed_progress is not None
            else (next_progress,)
        )
        return self._commit(command, session=session, activities=(StoredActivity(command.activity_id, activity),),
            activity_progress=progress_changes, expected_activity_progress=expected_progress,
            expected_absent_activity_ids=(command.activity_id,),
            events=(TutoringEvent("activity-selected", command.session_id, activity.objective_id, command.activity_id, f"{activity.template_id}:{reason}:{source_activity.difficulty if source_id else activity.difficulty}->{activity.difficulty}"),),
            expected_regulation_revision=state.regulation_revision,
            regulation_mutation=RegulationMutation(
                state.regulation_revision + 1, 0, 0,
                state.pending_regulation_event.strategy if state.pending_regulation_event else ExecutedRegulationAction.CAP_CHOICE,
                close_outcome=RegulationOutcome.UNKNOWN,
            ), payload=activity)

    def propose_evidence(self, command: ProposeEvidence) -> CommandResult:
        state, error = self._state(command)
        if error: return error
        try:
            stored_observation = self._repository.load_observation(command.session_id, command.observation_id)
        except Exception:
            return self._failed(command, "persistence-unavailable")
        if stored_observation is None:
            return self._rejected(command, "observation-not-found")
        observation = stored_observation.observation
        if observation.session_id != command.session_id or observation.learner_id != state.session.learner_id:
            return self._rejected(command, "observation-outside-session")
        if observation.objective_id not in state.session.authorised_objective_ids:
            return self._rejected(command, "objective-not-authorised")
        try:
            activity = self._repository.load_activity(command.session_id, observation.activity_id)
        except Exception:
            return self._failed(command, "persistence-unavailable")
        if activity is None:
            return self._rejected(command, "observation-activity-not-found")
        if activity.objective_id != observation.objective_id:
            return self._rejected(command, "observation-activity-mismatch")
        try:
            evidence = EvidenceRecord.initial(
                evidence_id=command.evidence_id,
                learner_id=state.session.learner_id,
                observation=observation,
                interpretation=command.interpretation,
                reason_for_retention=command.reason_for_retention,
            )
        except (TypeError, ValueError):
            return self._rejected(command, "domain-validation-error")
        session = replace(state.session, version=state.session.version + 1)
        return self._commit(command, session=session, evidence=(evidence,),
            expected_observations=(ObservationExpectation(command.observation_id, stored_observation.version),),
            events=(TutoringEvent("evidence-proposed", command.session_id, observation.objective_id, observation.activity_id, command.evidence_id),), payload=evidence)

    def propose_profile_change(self, command: ProposeProfileChange) -> CommandResult:
        state, error = self._state(command)
        if error: return error
        if command.objective_id not in state.session.authorised_objective_ids:
            return self._rejected(command, "objective-not-authorised")
        try:
            estimate = self._repository.load_estimate(state.session.learner_id, command.objective_id)
            evidence = self._repository.load_evidence(state.session.learner_id, command.objective_id)
        except Exception:
            return self._failed(command, "persistence-unavailable")
        if estimate is None: return self._rejected(command, "estimate-not-found")
        try:
            proposal = self._progression_policy.propose_change(estimate, evidence)
        except (TypeError, ValueError):
            return self._rejected(command, "domain-validation-error")
        if proposal is None: return self._rejected(command, "insufficient-evidence")
        session = replace(state.session, version=state.session.version + 1)
        return self._commit(command, session=session, profile_change_proposals=(proposal,),
            events=(TutoringEvent("profile-change-proposed", command.session_id, command.objective_id, detail=proposal.to_state.value),), payload=proposal)

    def end_session(self, command: EndSession) -> CommandResult:
        state, error = self._state(command)
        if error: return error
        try:
            session = state.session.end(reason=command.reason)
        except (TypeError, ValueError):
            return self._rejected(command, "domain-validation-error")
        return self._commit(command, session=session, events=(TutoringEvent("session-ended", command.session_id, detail=command.reason),),
            expected_regulation_revision=state.regulation_revision,
            regulation_mutation=RegulationMutation(
                state.regulation_revision + 1, 0, state.activity_sequence,
                state.pending_regulation_event.strategy if state.pending_regulation_event else ExecutedRegulationAction.CAP_CHOICE,
                close_outcome=(RegulationOutcome.STOPPED if command.reason == "stop-requested" else RegulationOutcome.UNKNOWN),
            ))

    def stop_now(self, command: EndSession) -> CommandResult:
        """Cancel locally first, then attempt the durable terminal transition."""

        self._runtime.cancel_generation(command.session_id)
        try:
            state = self._repository.load_state(command.session_id)
        except Exception:
            return self._failed(command, "persistence-unavailable")
        if state is None:
            return self._rejected(command, "session-not-found")
        try:
            session = state.session.request_stop(reason=command.reason)
            result = CommandResult(command.command_id, CommandStatus.APPLIED, "applied")
            batch = MutationBatch(
                command_id=command.command_id, command_fingerprint=self._fingerprint(command),
                session_id=command.session_id, expected_session_version=state.session.version,
                expected_profile_version=state.profile_version, result=result, session=session,
                events=(TutoringEvent("session-stop-requested", command.session_id, detail=command.reason),),
                expected_regulation_revision=state.regulation_revision,
                regulation_mutation=RegulationMutation(
                    state.regulation_revision + 1, 0, state.activity_sequence,
                    state.pending_regulation_event.strategy if state.pending_regulation_event else ExecutedRegulationAction.CAP_CHOICE,
                    close_outcome=(RegulationOutcome.STOPPED if command.reason == "stop-requested" else RegulationOutcome.UNKNOWN),
                ),
            )
            decision = self._repository.commit_once(batch)
        except Exception:
            return self._failed(command, "persistence-unavailable")
        if decision.outcome is CommitOutcome.APPLIED and decision.result is not None:
            return decision.result
        if decision.outcome is CommitOutcome.REPLAYED and decision.result is not None:
            return decision.result.as_replay()
        return self._rejected(command, decision.reason or "stop-persistence-conflict")
