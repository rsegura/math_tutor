"""Transactional SQLite implementation of the application persistence port."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
import importlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from math_tutor.application.ports import (ActivityProgress, CommitDecision, MutationBatch, PersistedTutoringState, StoredCommandResult, StoredObservation)
from math_tutor.application.results import CommandResult
from math_tutor.domain.activities import Activity
from math_tutor.domain.evidence import EvidenceRecord, EvidenceRevision, Observation
from math_tutor.domain.learning import LearningPlan, LearningSession, SkillEstimate


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


def _tree(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {"$dataclass": f"{type(value).__module__}:{type(value).__qualname__}", "fields": {field.name: _tree(getattr(value, field.name)) for field in fields(value)}}
    if isinstance(value, Enum):
        return {"$enum": f"{type(value).__module__}:{type(value).__qualname__}", "value": value.value}
    if isinstance(value, tuple):
        return {"$tuple": [_tree(item) for item in value]}
    if isinstance(value, dict) or hasattr(value, "items"):
        return {"$mapping": [[_tree(key), _tree(item)] for key, item in value.items()]}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported durable value {type(value).__qualname__}")


def _resolve(name: str) -> type:
    module_name, qualname = name.split(":", 1)
    if not module_name.startswith("math_tutor."):
        raise ValueError("durable type is outside math_tutor")
    target: Any = importlib.import_module(module_name)
    for part in qualname.split("."):
        target = getattr(target, part)
    return target


def _from_tree(value: Any) -> Any:
    if isinstance(value, list):
        return [_from_tree(item) for item in value]
    if not isinstance(value, dict):
        return value
    if "$enum" in value:
        return _resolve(value["$enum"])(value["value"])
    if "$tuple" in value:
        return tuple(_from_tree(item) for item in value["$tuple"])
    if "$mapping" in value:
        return {_from_tree(key): _from_tree(item) for key, item in value["$mapping"]}
    if "$dataclass" in value:
        cls = _resolve(value["$dataclass"])
        return cls(**{key: _from_tree(item) for key, item in value["fields"].items()})
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
            db.execute("INSERT INTO learning_sessions(session_id,learner_id,plan_id,plan_version,session_json,version,profile_version) VALUES(?,?,?,?,?,?,?)", (session.session_id, session.learner_id, session.plan_id, session.plan_version, _dump(session), session.version, profile_version))

    def save_estimate(self, estimate: SkillEstimate) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO skill_estimates(learner_id,objective_id,estimate_json,version) VALUES(?,?,?,?) ON CONFLICT(learner_id,objective_id) DO UPDATE SET estimate_json=excluded.estimate_json,version=excluded.version", (estimate.learner_id, estimate.objective_id, _dump(estimate), estimate.version))

    def save_activity_progress(self, session_id: str, *progress: ActivityProgress) -> None:
        with self._connect() as db:
            for item in progress:
                db.execute("INSERT INTO activity_progress(session_id,activity_id,progress_json,version) VALUES(?,?,?,?) ON CONFLICT(session_id,activity_id) DO UPDATE SET progress_json=excluded.progress_json,version=excluded.version", (session_id, item.activity_id, _dump(item), item.version))

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
        session = db.execute("SELECT version,profile_version FROM learning_sessions WHERE session_id=?", (batch.session_id,)).fetchone()
        if session is None: return "session-not-found"
        if session[0] != batch.expected_session_version: return "stale-session-version"
        if session[1] != batch.expected_profile_version: return "stale-profile-version"
        for expected in batch.expected_observations:
            row = db.execute("SELECT version FROM observations WHERE session_id=? AND observation_id=?", (batch.session_id, expected.observation_id)).fetchone()
            if row is None or row[0] != expected.version: return "stale-observation-version"
        for expected in batch.expected_activity_progress:
            row = db.execute("SELECT version FROM activity_progress WHERE session_id=? AND activity_id=?", (batch.session_id, expected.activity_id)).fetchone()
            if row is None or row[0] != expected.version: return "stale-activity-progress-version"
        for activity_id in batch.expected_absent_activity_ids:
            if db.execute("SELECT 1 FROM activities WHERE session_id=? AND activity_id=?", (batch.session_id, activity_id)).fetchone(): return "activity-id-already-exists"
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
                db.execute("INSERT INTO activities(session_id,activity_id,activity_json) VALUES(?,?,?)", (batch.session_id, stored.activity_id, _dump(stored.activity)))
            for observation in batch.observations:
                db.execute("INSERT INTO observations(observation_id,session_id,learner_id,objective_id,observation_json,version) VALUES(?,?,?,?,?,1)", (observation.observation_id, observation.session_id, observation.learner_id, observation.objective_id, _dump(observation)))
            for evidence in batch.evidence:
                db.execute("INSERT INTO evidence_records(evidence_id,observation_id,learner_id,objective_id,reason_for_retention) VALUES(?,?,?,?,?)", (evidence.evidence_id, evidence.observation.observation_id, evidence.learner_id, evidence.observation.objective_id, evidence.reason_for_retention))
                for revision in evidence.interpretations:
                    db.execute("INSERT INTO evidence_interpretations(evidence_id,version,interpretation,reason) VALUES(?,?,?,?)", (evidence.evidence_id, revision.version, revision.interpretation, revision.reason))
            for proposal in batch.profile_change_proposals:
                db.execute("INSERT INTO profile_change_proposals(learner_id,objective_id,proposal_json,estimate_version,policy_version) VALUES(?,?,?,?,?)", (proposal.learner_id, proposal.objective_id, _dump(proposal), proposal.estimate_version, proposal.policy_version))
            for progress in batch.activity_progress:
                db.execute("INSERT INTO activity_progress(session_id,activity_id,progress_json,version) VALUES(?,?,?,?) ON CONFLICT(session_id,activity_id) DO UPDATE SET progress_json=excluded.progress_json,version=excluded.version", (batch.session_id, progress.activity_id, _dump(progress), progress.version))
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
            db.execute("INSERT INTO therapist_reviews(review_id,version,learner_id,session_id,review_json) VALUES(?,?,?,?,?)", (review_id,version,learner_id,session_id,_dump(review)))

    def load_therapist_reviews(self, review_id: str) -> tuple[TherapistReviewRecord, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT review_id,version,learner_id,session_id,review_json FROM therapist_reviews WHERE review_id=? ORDER BY version", (review_id,)).fetchall()
        return tuple(TherapistReviewRecord(row[0], row[1], row[2], row[3], _load(row[4])) for row in rows)

    def append_profile_revision(self, revision_id: str, learner_id: str, profile_version: int, revision: object, *, policy_version: str) -> None:
        with self._connect() as db:
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

    def save_evidence_clip(self, clip_id: str, evidence_id: str, learner_id: str, session_id: str, *, duration_seconds: float, storage_key: str, expires_at: str) -> None:
        if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int,float)) or not 0 < duration_seconds <= 30:
            raise ValueError("clip duration must be greater than zero and at most 30 seconds")
        with self._connect() as db:
            db.execute("INSERT INTO evidence_clips(clip_id,evidence_id,learner_id,session_id,duration_seconds,storage_key,expires_at) VALUES(?,?,?,?,?,?,?)", (clip_id,evidence_id,learner_id,session_id,duration_seconds,storage_key,expires_at))

    def load_evidence_clip(self, clip_id: str) -> EvidenceClipRecord | None:
        with self._connect() as db:
            row = db.execute("SELECT clip_id,evidence_id,learner_id,session_id,duration_seconds,storage_key,expires_at FROM evidence_clips WHERE clip_id=?", (clip_id,)).fetchone()
        return EvidenceClipRecord(*row) if row else None
