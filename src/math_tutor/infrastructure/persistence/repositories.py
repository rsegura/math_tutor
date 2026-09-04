"""Transactional SQLite implementation of the application persistence port."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
import json
from pathlib import Path
import sqlite3
from typing import Any

from math_tutor.application.ports import (ActivityProgress, CommitDecision, MutationBatch, PersistedTutoringState, StoredCommandResult, StoredObservation)
from math_tutor.application.results import CommandResult, CommandStatus
from math_tutor.domain.activities import Activity, AnswerInputStatus, StructuredAnswer
from math_tutor.domain.evidence import EvidenceRecord, EvidenceRevision, Observation, ObservationOutcome, TranscriptionReliabilityPolicy
from math_tutor.domain.learning import CompetencyState, LearningPlan, LearningSession, PresentationProfile, ProposedProfileChange, SkillEstimate
from math_tutor.domain.templates import ExpectedAnswerKind


@dataclass(frozen=True, slots=True)
class LearnerRecord:
    learner_id: str
    curriculum_snapshot: str
    curriculum_version: str


@dataclass(frozen=True, slots=True)
class EvidenceClipRecord:
    clip_id: str
    evidence_id: str
    learner_id: str
    session_id: str
    duration_seconds: float
    storage_key: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class TherapistReviewRecord:
    review_id: str
    version: int
    learner_id: str
    session_id: str | None
    review: object


@dataclass(frozen=True, slots=True)
class ProfileRevisionRecord:
    revision_id: str
    learner_id: str
    profile_version: int
    revision: object
    policy_version: str


@dataclass(frozen=True, slots=True)
class PersistedEvent:
    kind: str
    session_id: str
    objective_id: str | None
    activity_id: str | None
    detail: str | None


@dataclass(frozen=True, slots=True)
class SessionAggregate:
    session: LearningSession
    plan: LearningPlan
    curriculum_snapshot: str
    curriculum_version: str
    policy_version: str
    profile_version: int
    activities: tuple[Activity, ...]
    observations: tuple[StoredObservation, ...]
    evidence: tuple[EvidenceRecord, ...]
    estimates: tuple[SkillEstimate, ...]
    activity_progress: tuple[ActivityProgress, ...]
    events: tuple[PersistedEvent, ...]
    proposals: tuple[object, ...]
    reviews: tuple[TherapistReviewRecord, ...]
    profile_revisions: tuple[ProfileRevisionRecord, ...]
    clips: tuple[EvidenceClipRecord, ...]


_WIRE_TYPES = {
    "command-result/v1": CommandResult,
    "activity/v1": Activity,
    "structured-answer/v1": StructuredAnswer,
    "learning-plan/v1": LearningPlan,
    "learning-session/v1": LearningSession,
    "presentation-profile/v1": PresentationProfile,
    "skill-estimate/v1": SkillEstimate,
    "profile-change-proposal/v1": ProposedProfileChange,
    "observation/v1": Observation,
    "transcription-policy/v1": TranscriptionReliabilityPolicy,
    "activity-progress/v1": ActivityProgress,
}
_WIRE_ENUMS = {
    "command-status/v1": CommandStatus,
    "answer-input-status/v1": AnswerInputStatus,
    "expected-answer-kind/v1": ExpectedAnswerKind,
    "observation-outcome/v1": ObservationOutcome,
    "competency-state/v1": CompetencyState,
}
_WIRE_TAGS = {value: key for key, value in _WIRE_TYPES.items()}
_WIRE_ENUM_TAGS = {value: key for key, value in _WIRE_ENUMS.items()}
_LEGACY_WIRE_TYPES = {
    "math_tutor.application.ports:ActivityProgress": ActivityProgress,
    "math_tutor.application.results:CommandResult": CommandResult,
    "math_tutor.domain.activities:Activity": Activity,
    "math_tutor.domain.activities:StructuredAnswer": StructuredAnswer,
    "math_tutor.domain.evidence:Observation": Observation,
    "math_tutor.domain.evidence:TranscriptionReliabilityPolicy": TranscriptionReliabilityPolicy,
    "math_tutor.domain.learning:LearningPlan": LearningPlan,
    "math_tutor.domain.learning:LearningSession": LearningSession,
    "math_tutor.domain.learning:PresentationProfile": PresentationProfile,
    "math_tutor.domain.learning:ProposedProfileChange": ProposedProfileChange,
    "math_tutor.domain.learning:SkillEstimate": SkillEstimate,
}
_LEGACY_WIRE_ENUMS = {
    "math_tutor.application.results:CommandStatus": CommandStatus,
    "math_tutor.domain.activities:AnswerInputStatus": AnswerInputStatus,
    "math_tutor.domain.evidence:ObservationOutcome": ObservationOutcome,
    "math_tutor.domain.learning:CompetencyState": CompetencyState,
    "math_tutor.domain.templates:ExpectedAnswerKind": ExpectedAnswerKind,
}


def _tree(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        tag = _WIRE_TAGS.get(type(value))
        if tag is None:
            raise TypeError(f"unsupported durable value {type(value).__qualname__}")
        return {"$type": tag, "fields": {field.name: _tree(getattr(value, field.name)) for field in fields(value)}}
    if isinstance(value, Enum):
        tag = _WIRE_ENUM_TAGS.get(type(value))
        if tag is None:
            raise TypeError(f"unsupported durable value {type(value).__qualname__}")
        return {"$enum": tag, "value": value.value}
    if isinstance(value, tuple):
        return {"$tuple": [_tree(item) for item in value]}
    if isinstance(value, dict) or hasattr(value, "items"):
        return {"$mapping": [[_tree(key), _tree(item)] for key, item in value.items()]}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported durable value {type(value).__qualname__}")


def _from_tree(value: Any) -> Any:
    if isinstance(value, list):
        return [_from_tree(item) for item in value]
    if not isinstance(value, dict):
        return value
    if "$enum" in value:
        cls = _WIRE_ENUMS.get(value["$enum"]) or _LEGACY_WIRE_ENUMS.get(
            value["$enum"]
        )
        if cls is None:
            raise ValueError(f"unknown durable type '{value['$enum']}'")
        return cls(value["value"])
    if "$tuple" in value:
        return tuple(_from_tree(item) for item in value["$tuple"])
    if "$mapping" in value:
        return {_from_tree(key): _from_tree(item) for key, item in value["$mapping"]}
    if "$type" in value:
        cls = _WIRE_TYPES.get(value["$type"])
        if cls is None:
            raise ValueError(f"unknown durable type '{value['$type']}'")
        return cls(**{key: _from_tree(item) for key, item in value["fields"].items()})
    if "$dataclass" in value:
        cls = _LEGACY_WIRE_TYPES.get(value["$dataclass"])
        if cls is None:
            raise ValueError(f"unknown durable type '{value['$dataclass']}'")
        return cls(
            **{key: _from_tree(item) for key, item in value["fields"].items()}
        )
    return {key: _from_tree(item) for key, item in value.items()}


def _dump(value: Any) -> str:
    return json.dumps(_tree(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: str) -> Any:
    return _from_tree(json.loads(value))


class SQLiteTutoringRepository:
    def __init__(self, database: str | Path):
        self.database = Path(database)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def save_learner(self, learner_id: str, *, curriculum_snapshot: str, curriculum_version: str) -> None:
        with self._connect() as db:
            prior = db.execute("SELECT curriculum_snapshot FROM curriculum_snapshots WHERE learner_id=? AND curriculum_version=?", (learner_id, curriculum_version)).fetchone()
            if prior is not None and prior[0] != curriculum_snapshot:
                raise ValueError("curriculum version already has a different snapshot")
            db.execute("INSERT INTO learners(learner_id,curriculum_snapshot,curriculum_version) VALUES(?,?,?) ON CONFLICT(learner_id) DO UPDATE SET curriculum_snapshot=excluded.curriculum_snapshot,curriculum_version=excluded.curriculum_version", (learner_id, curriculum_snapshot, curriculum_version))
            db.execute("INSERT INTO curriculum_snapshots(learner_id,curriculum_version,curriculum_snapshot) VALUES(?,?,?) ON CONFLICT(learner_id,curriculum_version) DO NOTHING", (learner_id, curriculum_version, curriculum_snapshot))

    def load_learner(self, learner_id: str) -> LearnerRecord | None:
        with self._connect() as db:
            row = db.execute("SELECT learner_id,curriculum_snapshot,curriculum_version FROM learners WHERE learner_id=?", (learner_id,)).fetchone()
        return LearnerRecord(*row) if row else None

    def load_curriculum_snapshot(self, learner_id: str, curriculum_version: str) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT curriculum_snapshot FROM curriculum_snapshots WHERE learner_id=? AND curriculum_version=?", (learner_id, curriculum_version)).fetchone()
        return row[0] if row else None

    def save_plan(self, plan: LearningPlan, *, policy_version: str) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO learning_plans(plan_id,version,learner_id,plan_json,policy_version) VALUES(?,?,?,?,?)", (plan.plan_id, plan.version, plan.learner_id, _dump(plan), policy_version))

    def load_plan(self, plan_id: str, version: int) -> LearningPlan | None:
        with self._connect() as db:
            row = db.execute("SELECT plan_json FROM learning_plans WHERE plan_id=? AND version=?", (plan_id, version)).fetchone()
        return _load(row[0]) if row else None

    def load_plan_policy_version(self, plan_id: str, version: int) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT policy_version FROM learning_plans WHERE plan_id=? AND version=?", (plan_id, version)).fetchone()
        return row[0] if row else None

    def save_session(self, session: LearningSession, *, profile_version: int) -> None:
        with self._connect() as db:
            owner = db.execute("SELECT 1 FROM learning_plans WHERE plan_id=? AND version=? AND learner_id=?", (session.plan_id, session.plan_version, session.learner_id)).fetchone()
            if owner is None:
                raise ValueError("session plan version is not owned by learner")
            db.execute("INSERT INTO learning_sessions(session_id,learner_id,plan_id,plan_version,session_json,version,profile_version) VALUES(?,?,?,?,?,?,?)", (session.session_id, session.learner_id, session.plan_id, session.plan_version, _dump(session), session.version, profile_version))

    def save_estimate(self, estimate: SkillEstimate, *, expected_version: int | None = None) -> None:
        with self._connect() as db:
            if db.execute("SELECT 1 FROM learners WHERE learner_id=?", (estimate.learner_id,)).fetchone() is None:
                raise ValueError("skill estimate learner does not exist")
            if expected_version is None:
                try:
                    db.execute("INSERT INTO skill_estimates(learner_id,objective_id,estimate_json,version) VALUES(?,?,?,?)", (estimate.learner_id, estimate.objective_id, _dump(estimate), estimate.version))
                except sqlite3.IntegrityError as error:
                    raise ValueError("skill estimate already exists; expected_version is required") from error
            else:
                if estimate.version != expected_version + 1:
                    raise ValueError("estimate version must advance by exactly one")
                cursor = db.execute("UPDATE skill_estimates SET estimate_json=?,version=? WHERE learner_id=? AND objective_id=? AND version=?", (_dump(estimate), estimate.version, estimate.learner_id, estimate.objective_id, expected_version))
                if cursor.rowcount != 1:
                    raise ValueError("stale estimate version")

    def save_activity_progress(self, session_id: str, *progress: ActivityProgress) -> None:
        """Bootstrap progress only; mutation updates belong to commit_once CAS."""
        with self._connect() as db:
            for item in progress:
                if db.execute("SELECT 1 FROM activities WHERE session_id=? AND activity_id=?", (session_id, item.activity_id)).fetchone() is None:
                    raise ValueError("activity progress requires a canonical activity")
                if item.version != 1:
                    raise ValueError("bootstrap activity progress must start at version 1")
                try:
                    db.execute("INSERT INTO activity_progress(session_id,activity_id,progress_json,version) VALUES(?,?,?,?)", (session_id, item.activity_id, _dump(item), item.version))
                except sqlite3.IntegrityError as error:
                    raise ValueError("activity progress already exists; use commit_once CAS") from error

    def load_command_result(self, command_id: str) -> StoredCommandResult | None:
        with self._connect() as db:
            row = db.execute("SELECT command_fingerprint,result_json FROM processed_commands WHERE command_id=?", (command_id,)).fetchone()
        return StoredCommandResult(row[0], _load(row[1])) if row else None

    def load_state(self, session_id: str) -> PersistedTutoringState | None:
        with self._connect() as db:
            row = db.execute("SELECT session_json,profile_version FROM learning_sessions WHERE session_id=?", (session_id,)).fetchone()
            if row is None: return None
            progress = tuple(_load(item[0]) for item in db.execute("SELECT progress_json FROM activity_progress WHERE session_id=? ORDER BY activity_id", (session_id,)))
        return PersistedTutoringState(_load(row[0]), row[1], progress)

    def load_activity(self, session_id: str, activity_id: str) -> Activity | None:
        with self._connect() as db:
            row = db.execute("SELECT activity_json FROM activities WHERE session_id=? AND activity_id=?", (session_id, activity_id)).fetchone()
        return _load(row[0]) if row else None

    def load_observation(self, session_id: str, observation_id: str) -> StoredObservation | None:
        with self._connect() as db:
            row = db.execute("SELECT observation_json,version FROM observations WHERE session_id=? AND observation_id=?", (session_id, observation_id)).fetchone()
        return StoredObservation(_load(row[0]), row[1]) if row else None

    def load_evidence(self, learner_id: str, objective_id: str) -> tuple[EvidenceRecord, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT e.evidence_id,e.reason_for_retention,o.observation_json FROM evidence_records e JOIN observations o ON o.observation_id=e.observation_id WHERE e.learner_id=? AND e.objective_id=? ORDER BY e.created_at,e.evidence_id", (learner_id, objective_id)).fetchall()
            result = []
            for row in rows:
                revisions = tuple(EvidenceRevision(item[0], item[1], item[2]) for item in db.execute("SELECT version,interpretation,reason FROM evidence_interpretations WHERE evidence_id=? ORDER BY version", (row[0],)))
                result.append(EvidenceRecord(row[0], learner_id, _load(row[2]), row[1], revisions))
        return tuple(result)

    def load_estimate(self, learner_id: str, objective_id: str) -> SkillEstimate | None:
        with self._connect() as db:
            row = db.execute("SELECT estimate_json FROM skill_estimates WHERE learner_id=? AND objective_id=?", (learner_id, objective_id)).fetchone()
        return _load(row[0]) if row else None

    def _conflict(self, db: sqlite3.Connection, batch: MutationBatch) -> str | None:
        session = db.execute("SELECT version,profile_version,learner_id,plan_id,plan_version,session_json FROM learning_sessions WHERE session_id=?", (batch.session_id,)).fetchone()
        if session is None: return "session-not-found"
        if session[0] != batch.expected_session_version: return "stale-session-version"
        if session[1] != batch.expected_profile_version: return "stale-profile-version"
        learner_id = session[2]
        current_session = _load(session[5])
        if batch.session is not None and batch.session.session_id != batch.session_id:
            return "batch-session-mismatch"
        if batch.session is not None and (batch.session.learner_id != learner_id or batch.session.plan_id != session[3] or batch.session.plan_version != session[4]):
            return "batch-learner-mismatch" if batch.session.learner_id != learner_id else "batch-plan-mismatch"
        if batch.session is not None and batch.session.version != batch.expected_session_version + 1:
            return "invalid-session-version"
        objective_scope = set(current_session.authorised_objective_ids)
        activity_ids = {item.activity_id for item in batch.activities}
        for stored in batch.activities:
            if not isinstance(stored.activity_id, str) or not stored.activity_id.strip():
                return "batch-activity-mismatch"
            if stored.activity.objective_id not in objective_scope:
                return "batch-objective-mismatch"
        for observation in batch.observations:
            if observation.session_id != batch.session_id:
                return "batch-session-mismatch"
            if observation.learner_id != learner_id:
                return "batch-learner-mismatch"
            if observation.objective_id not in objective_scope:
                return "batch-objective-mismatch"
            existing_activity = db.execute("SELECT activity_json FROM activities WHERE session_id=? AND activity_id=?", (batch.session_id, observation.activity_id)).fetchone()
            if observation.activity_id not in activity_ids and existing_activity is None:
                return "batch-activity-mismatch"
            canonical_activity = next((item.activity for item in batch.activities if item.activity_id == observation.activity_id), None)
            if canonical_activity is None and existing_activity is not None:
                canonical_activity = _load(existing_activity[0])
            if canonical_activity is None or canonical_activity.objective_id != observation.objective_id:
                return "batch-objective-mismatch"
        observations = {item.observation_id: item for item in batch.observations}
        for evidence in batch.evidence:
            observation = observations.get(evidence.observation.observation_id)
            if observation is None:
                row = db.execute("SELECT observation_json FROM observations WHERE observation_id=? AND session_id=?", (evidence.observation.observation_id, batch.session_id)).fetchone()
                observation = _load(row[0]) if row else None
            if observation is None or observation != evidence.observation:
                return "batch-evidence-mismatch"
            if evidence.learner_id != learner_id:
                return "batch-learner-mismatch"
        for proposal in batch.profile_change_proposals:
            if proposal.learner_id != learner_id:
                return "batch-learner-mismatch"
            if proposal.objective_id not in objective_scope:
                return "batch-objective-mismatch"
            for evidence_id, observation_id in zip(proposal.evidence_ids, proposal.observation_ids, strict=True):
                candidate = next((item for item in batch.evidence if item.evidence_id == evidence_id), None)
                if candidate is None:
                    row = db.execute(
                        "SELECT e.observation_id,e.learner_id,e.session_id,e.objective_id FROM evidence_records e WHERE e.evidence_id=?",
                        (evidence_id,),
                    ).fetchone()
                    if row is None or (
                        row[0] != observation_id
                        or row[1] != learner_id
                        or row[3] != proposal.objective_id
                    ):
                        return "batch-proposal-evidence-mismatch"
                elif (
                    candidate.observation.observation_id != observation_id
                    or candidate.learner_id != learner_id
                    or candidate.observation.session_id != batch.session_id
                    or candidate.observation.objective_id != proposal.objective_id
                ):
                    return "batch-proposal-evidence-mismatch"
        for event in batch.events:
            if event.session_id != batch.session_id:
                return "batch-session-mismatch"
            if event.objective_id is not None and event.objective_id not in objective_scope:
                return "batch-objective-mismatch"
            if event.activity_id is not None:
                canonical = next((item.activity for item in batch.activities if item.activity_id == event.activity_id), None)
                if canonical is None:
                    row = db.execute("SELECT activity_json FROM activities WHERE session_id=? AND activity_id=?", (batch.session_id, event.activity_id)).fetchone()
                    canonical = _load(row[0]) if row else None
                if canonical is None:
                    return "batch-activity-mismatch"
                if event.objective_id is not None and canonical.objective_id != event.objective_id:
                    return "batch-objective-mismatch"
        for expected in batch.expected_observations:
            row = db.execute("SELECT version FROM observations WHERE session_id=? AND observation_id=?", (batch.session_id, expected.observation_id)).fetchone()
            if row is None or row[0] != expected.version: return "stale-observation-version"
        for expected in batch.expected_activity_progress:
            row = db.execute("SELECT version FROM activity_progress WHERE session_id=? AND activity_id=?", (batch.session_id, expected.activity_id)).fetchone()
            if row is None or row[0] != expected.version: return "stale-activity-progress-version"
        for activity_id in batch.expected_absent_activity_ids:
            if db.execute("SELECT 1 FROM activities WHERE session_id=? AND activity_id=?", (batch.session_id, activity_id)).fetchone(): return "activity-id-already-exists"
        progress_ids = {item.activity_id for item in batch.activity_progress}
        expected_ids = {item.activity_id for item in batch.expected_activity_progress}
        absent_ids = set(batch.expected_absent_activity_ids)
        if progress_ids - expected_ids - absent_ids:
            return "missing-activity-progress-expectation"
        for progress in batch.activity_progress:
            has_activity = db.execute("SELECT 1 FROM activities WHERE session_id=? AND activity_id=?", (batch.session_id, progress.activity_id)).fetchone()
            has_progress = db.execute("SELECT 1 FROM activity_progress WHERE session_id=? AND activity_id=?", (batch.session_id, progress.activity_id)).fetchone()
            if progress.activity_id not in activity_ids and has_activity is None and has_progress is None:
                return "batch-activity-mismatch"
            expectation = next((item for item in batch.expected_activity_progress if item.activity_id == progress.activity_id), None)
            if expectation is not None and progress.version != expectation.version + 1:
                return "invalid-activity-progress-version"
            if expectation is None and progress.version != 1:
                return "invalid-activity-progress-version"
        return None

    def commit_once(self, batch: MutationBatch) -> CommitDecision:
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT command_fingerprint,result_json FROM processed_commands WHERE command_id=?", (batch.command_id,)).fetchone()
            if prior:
                db.rollback()
                result = _load(prior[1])
                return CommitDecision.replayed(result, prior[0]) if prior[0] == batch.command_fingerprint else CommitDecision.collision(result, prior[0])
            conflict = self._conflict(db, batch)
            if conflict:
                db.rollback()
                return CommitDecision.conflict(conflict)
            if batch.session is not None:
                cursor = db.execute("UPDATE learning_sessions SET session_json=?,version=? WHERE session_id=? AND version=?", (_dump(batch.session), batch.session.version, batch.session_id, batch.expected_session_version))
                if cursor.rowcount != 1: raise sqlite3.IntegrityError("lost session update")
            for stored in batch.activities:
                db.execute("INSERT INTO activities(session_id,activity_id,objective_id,activity_json) VALUES(?,?,?,?)", (batch.session_id, stored.activity_id, stored.activity.objective_id, _dump(stored.activity)))
            for observation in batch.observations:
                db.execute("INSERT INTO observations(observation_id,session_id,learner_id,objective_id,activity_id,observation_json,version) VALUES(?,?,?,?,?,?,1)", (observation.observation_id, observation.session_id, observation.learner_id, observation.objective_id, observation.activity_id, _dump(observation)))
            for evidence in batch.evidence:
                db.execute("INSERT INTO evidence_records(evidence_id,observation_id,learner_id,session_id,objective_id,reason_for_retention) VALUES(?,?,?,?,?,?)", (evidence.evidence_id, evidence.observation.observation_id, evidence.learner_id, evidence.observation.session_id, evidence.observation.objective_id, evidence.reason_for_retention))
                for revision in evidence.interpretations:
                    db.execute("INSERT INTO evidence_interpretations(evidence_id,version,interpretation,reason) VALUES(?,?,?,?)", (evidence.evidence_id, revision.version, revision.interpretation, revision.reason))
            for proposal in batch.profile_change_proposals:
                cursor = db.execute("INSERT INTO profile_change_proposals(learner_id,session_id,objective_id,proposal_json,estimate_version,policy_version) VALUES(?,?,?,?,?,?)", (proposal.learner_id, batch.session_id, proposal.objective_id, _dump(proposal), proposal.estimate_version, proposal.policy_version))
                for evidence_id, observation_id in zip(proposal.evidence_ids, proposal.observation_ids, strict=True):
                    source = db.execute(
                        "SELECT session_id FROM evidence_records WHERE evidence_id=? AND observation_id=? AND learner_id=? AND objective_id=?",
                        (evidence_id, observation_id, proposal.learner_id, proposal.objective_id),
                    ).fetchone()
                    if source is None:
                        raise sqlite3.IntegrityError("proposal evidence disappeared")
                    db.execute("INSERT INTO proposal_evidence(proposal_id,evidence_id,observation_id,learner_id,source_session_id,objective_id) VALUES(?,?,?,?,?,?)", (cursor.lastrowid, evidence_id, observation_id, proposal.learner_id, source[0], proposal.objective_id))
            for progress in batch.activity_progress:
                expectation = next((item for item in batch.expected_activity_progress if item.activity_id == progress.activity_id), None)
                if expectation is None:
                    db.execute("INSERT INTO activity_progress(session_id,activity_id,progress_json,version) VALUES(?,?,?,?)", (batch.session_id, progress.activity_id, _dump(progress), progress.version))
                else:
                    cursor = db.execute("UPDATE activity_progress SET progress_json=?,version=? WHERE session_id=? AND activity_id=? AND version=?", (_dump(progress), progress.version, batch.session_id, progress.activity_id, expectation.version))
                    if cursor.rowcount != 1:
                        raise sqlite3.IntegrityError("lost activity progress update")
            for event in batch.events:
                db.execute("INSERT INTO tutoring_events(command_id,kind,session_id,objective_id,activity_id,detail) VALUES(?,?,?,?,?,?)", (batch.command_id,event.kind,event.session_id,event.objective_id,event.activity_id,event.detail))
            canonical = _dump(batch.result)
            db.execute("INSERT INTO processed_commands(command_id,command_fingerprint,result_json) VALUES(?,?,?)", (batch.command_id,batch.command_fingerprint,canonical))
            db.commit()
            return CommitDecision.applied(_load(canonical), batch.command_fingerprint)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def append_therapist_review(self, review_id: str, version: int, learner_id: str, session_id: str | None, review: object) -> None:
        with self._connect() as db:
            if db.execute("SELECT 1 FROM learners WHERE learner_id=?", (learner_id,)).fetchone() is None:
                raise ValueError("review learner does not exist")
            if session_id is not None and db.execute("SELECT 1 FROM learning_sessions WHERE session_id=? AND learner_id=?", (session_id, learner_id)).fetchone() is None:
                raise ValueError("review session is not owned by learner")
            db.execute("INSERT INTO therapist_reviews(review_id,version,learner_id,session_id,review_json) VALUES(?,?,?,?,?)", (review_id,version,learner_id,session_id,_dump(review)))

    def load_therapist_reviews(self, review_id: str) -> tuple[TherapistReviewRecord, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT review_id,version,learner_id,session_id,review_json FROM therapist_reviews WHERE review_id=? ORDER BY version", (review_id,)).fetchall()
        return tuple(TherapistReviewRecord(row[0], row[1], row[2], row[3], _load(row[4])) for row in rows)

    def append_profile_revision(self, revision_id: str, learner_id: str, profile_version: int, revision: object, *, policy_version: str) -> None:
        with self._connect() as db:
            if db.execute("SELECT 1 FROM learners WHERE learner_id=?", (learner_id,)).fetchone() is None:
                raise ValueError("profile revision learner does not exist")
            db.execute("INSERT INTO profile_revisions(revision_id,learner_id,profile_version,revision_json,policy_version) VALUES(?,?,?,?,?)", (revision_id,learner_id,profile_version,_dump(revision),policy_version))

    def load_profile_revisions(self, learner_id: str) -> tuple[ProfileRevisionRecord, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT revision_id,learner_id,profile_version,revision_json,policy_version FROM profile_revisions WHERE learner_id=? ORDER BY profile_version", (learner_id,)).fetchall()
        return tuple(ProfileRevisionRecord(row[0], row[1], row[2], _load(row[3]), row[4]) for row in rows)

    def load_profile_change_proposals(self, learner_id: str, objective_id: str) -> tuple[object, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT proposal_json FROM profile_change_proposals WHERE learner_id=? AND objective_id=? ORDER BY proposal_id", (learner_id, objective_id)).fetchall()
        return tuple(_load(row[0]) for row in rows)

    def load_events(self, session_id: str) -> tuple[PersistedEvent, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT kind,session_id,objective_id,activity_id,detail FROM tutoring_events WHERE session_id=? ORDER BY event_id", (session_id,)).fetchall()
        return tuple(PersistedEvent(*row) for row in rows)

    def append_interpretation_revision(self, evidence_id: str, *, expected_version: int, interpretation: str, reason: str) -> EvidenceRevision:
        revision = EvidenceRevision(expected_version + 1, interpretation, reason)
        with self._connect() as db:
            current = db.execute("SELECT COALESCE(MAX(version),0) FROM evidence_interpretations WHERE evidence_id=?", (evidence_id,)).fetchone()[0]
            if current != expected_version:
                raise ValueError("stale interpretation version")
            try:
                db.execute("INSERT INTO evidence_interpretations(evidence_id,version,interpretation,reason) VALUES(?,?,?,?)", (evidence_id, revision.version, revision.interpretation, revision.reason))
            except sqlite3.IntegrityError as error:
                raise ValueError("stale interpretation version") from error
        return revision

    def save_evidence_clip(self, clip_id: str, evidence_id: str, learner_id: str | None = None, session_id: str | None = None, *, duration_seconds: float, storage_key: str, expires_at: str) -> None:
        if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int,float)) or not 0 < duration_seconds <= 30:
            raise ValueError("clip duration must be greater than zero and at most 30 seconds")
        with self._connect() as db:
            owner = db.execute("SELECT o.learner_id,o.session_id FROM evidence_records e JOIN observations o ON o.observation_id=e.observation_id WHERE e.evidence_id=?", (evidence_id,)).fetchone()
            if owner is None:
                raise ValueError("evidence does not exist")
            if learner_id is not None and learner_id != owner[0] or session_id is not None and session_id != owner[1]:
                raise ValueError("clip ownership does not match canonical evidence")
            db.execute("INSERT INTO evidence_clips(clip_id,evidence_id,learner_id,session_id,duration_seconds,storage_key,expires_at) VALUES(?,?,?,?,?,?,?)", (clip_id,evidence_id,owner[0],owner[1],duration_seconds,storage_key,expires_at))

    def load_evidence_clip(self, clip_id: str) -> EvidenceClipRecord | None:
        with self._connect() as db:
            row = db.execute("SELECT clip_id,evidence_id,learner_id,session_id,duration_seconds,storage_key,expires_at FROM evidence_clips WHERE clip_id=?", (clip_id,)).fetchone()
        return EvidenceClipRecord(*row) if row else None

    def load_session_aggregate(self, session_id: str) -> SessionAggregate | None:
        with self._connect() as db:
            session_row = db.execute("SELECT session_json,profile_version,learner_id,plan_id,plan_version FROM learning_sessions WHERE session_id=?", (session_id,)).fetchone()
            if session_row is None:
                return None
            learner_id, plan_id, plan_version = session_row[2], session_row[3], session_row[4]
            learner = db.execute("SELECT curriculum_snapshot,curriculum_version FROM learners WHERE learner_id=?", (learner_id,)).fetchone()
            plan_row = db.execute("SELECT plan_json,policy_version FROM learning_plans WHERE plan_id=? AND version=? AND learner_id=?", (plan_id, plan_version, learner_id)).fetchone()
            activities = tuple(_load(row[0]) for row in db.execute("SELECT activity_json FROM activities WHERE session_id=? ORDER BY activity_id", (session_id,)))
            observations = tuple(StoredObservation(_load(row[0]), row[1]) for row in db.execute("SELECT observation_json,version FROM observations WHERE session_id=? ORDER BY observation_id", (session_id,)))
            evidence_ids = [row[0] for row in db.execute("SELECT e.evidence_id FROM evidence_records e JOIN observations o ON o.observation_id=e.observation_id WHERE o.session_id=? ORDER BY e.evidence_id", (session_id,))]
            evidence = tuple(record for objective in _load(session_row[0]).authorised_objective_ids for record in self.load_evidence(learner_id, objective) if record.evidence_id in evidence_ids)
            authorised = set(_load(session_row[0]).authorised_objective_ids)
            estimates = tuple(
                _load(row[1])
                for row in db.execute("SELECT objective_id,estimate_json FROM skill_estimates WHERE learner_id=? ORDER BY objective_id", (learner_id,))
                if row[0] in authorised
            )
            progress = tuple(_load(row[0]) for row in db.execute("SELECT progress_json FROM activity_progress WHERE session_id=? ORDER BY activity_id", (session_id,)))
            events = tuple(PersistedEvent(*row) for row in db.execute("SELECT kind,session_id,objective_id,activity_id,detail FROM tutoring_events WHERE session_id=? ORDER BY event_id", (session_id,)))
            proposals = tuple(_load(row[0]) for row in db.execute("SELECT proposal_json FROM profile_change_proposals WHERE learner_id=? AND session_id=? ORDER BY proposal_id", (learner_id, session_id)))
            reviews = tuple(TherapistReviewRecord(row[0],row[1],row[2],row[3],_load(row[4])) for row in db.execute("SELECT review_id,version,learner_id,session_id,review_json FROM therapist_reviews WHERE session_id=? ORDER BY review_id,version", (session_id,)))
            revisions = tuple(ProfileRevisionRecord(row[0],row[1],row[2],_load(row[3]),row[4]) for row in db.execute("SELECT revision_id,learner_id,profile_version,revision_json,policy_version FROM profile_revisions WHERE learner_id=? ORDER BY profile_version", (learner_id,)))
            clips = tuple(EvidenceClipRecord(*row) for row in db.execute("SELECT clip_id,evidence_id,learner_id,session_id,duration_seconds,storage_key,expires_at FROM evidence_clips WHERE session_id=? ORDER BY clip_id", (session_id,)))
        return SessionAggregate(_load(session_row[0]), _load(plan_row[0]), learner[0], learner[1], plan_row[1], session_row[1], activities, observations, evidence, estimates, progress, events, proposals, reviews, revisions, clips)
