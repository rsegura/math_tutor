"""Validated mutation fence for tutoring commands."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum
from hashlib import sha256
import json

from math_tutor.application.ports import (
    CommitOutcome,
    MutationBatch,
    ObservationExpectation,
    StoredActivity,
    TutoringEvent,
    TutoringRepository,
)
from math_tutor.application.results import CommandResult, CommandStatus
from math_tutor.application.session_runtime import SessionRuntime
from math_tutor.domain.activities import InvalidActivity, StructuredAnswer, generate_activity
from math_tutor.domain.evidence import (
    EvidenceRecord,
    Observation,
    TranscriptionReliabilityPolicy,
)
from math_tutor.domain.learning import ProgressionPolicy
from math_tutor.domain.mathematics import verify_answer
from math_tutor.domain.templates import ActivityTemplateCatalog, InvalidActivityTemplate


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
class SelectNextActivity(Command):
    activity_id: str
    objective_id: str
    template_id: str
    seed: int
    difficulty: int


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
                assistance_level=command.assistance_level,
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
        session = replace(state.session, version=state.session.version + 1)
        return self._commit(command, session=session, observations=(observation,), evidence=evidence,
            events=(TutoringEvent("answer-recorded", command.session_id, activity.objective_id, command.activity_id, check.reason),),
            payload=check)

    def commit_hint(self, command: CommitHint) -> CommandResult:
        state, error = self._state(command)
        if error: return error
        try: activity = self._repository.load_activity(command.session_id, command.activity_id)
        except Exception: return self._failed(command, "persistence-unavailable")
        if activity is None: return self._rejected(command, "activity-not-found")
        if activity.objective_id not in state.session.authorised_objective_ids:
            return self._rejected(command, "objective-not-authorised")
        if command.hint_index < 0 or command.hint_index >= len(activity.hint_ids) or activity.hint_ids[command.hint_index] != command.hint_id:
            return self._rejected(command, "hint-not-reviewed-at-index")
        session = replace(state.session, version=state.session.version + 1)
        return self._commit(command, session=session, events=(TutoringEvent("hint-committed", command.session_id, activity.objective_id, command.activity_id, command.hint_id),))

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
            activity = generate_activity(template, seed=command.seed, difficulty=command.difficulty)
        except (InvalidActivity, TypeError, ValueError):
            return self._rejected(command, "activity-generation-invalid")
        session = replace(state.session, version=state.session.version + 1)
        return self._commit(command, session=session, activities=(StoredActivity(command.activity_id, activity),),
            events=(TutoringEvent("activity-selected", command.session_id, activity.objective_id, command.activity_id, activity.template_id),), payload=activity)

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
        return self._commit(command, session=session, events=(TutoringEvent("session-ended", command.session_id, detail=command.reason),))
