import sqlite3
from pathlib import Path

import pytest

from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository
from math_tutor.domain.learning import (
    CompetencyState, LearningPlan, LearningSession, PresentationProfile,
    ProposedProfileChange, SkillEstimate,
)
from math_tutor.domain.activities import Activity, StructuredAnswer
from math_tutor.domain.evidence import Observation, ObservationOutcome, TranscriptionReliabilityPolicy
from math_tutor.domain.templates import ExpectedAnswerKind
from math_tutor.application.ports import MutationBatch, TutoringEvent
from math_tutor.application.results import CommandResult, CommandStatus


def test_failing_migration_does_not_leave_schema_or_version(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_broken.sql").write_text(
        "CREATE TABLE leaked(value TEXT); INSERT INTO missing(value) VALUES ('x');",
        encoding="utf-8",
    )
    database = tmp_path / "atomic.db"
    with pytest.raises(sqlite3.OperationalError):
        migrate(database, migration_dir=migrations)
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT name FROM sqlite_master WHERE name='leaked'").fetchone() is None
    assert connection.execute("SELECT 1 FROM schema_migrations WHERE version=1").fetchone() is None


def test_v1_database_is_upgraded_without_rewriting_history_or_losing_data(tmp_path):
    database = tmp_path / "upgrade.db"
    original_v1 = Path("src/math_tutor/infrastructure/persistence/migrations/0001_initial.sql").read_text()
    connection = sqlite3.connect(database)
    connection.executescript(original_v1)
    connection.execute("INSERT INTO schema_migrations(version) VALUES(1)")
    connection.execute("INSERT INTO learners(learner_id,curriculum_snapshot,curriculum_version) VALUES('learner-1','old','v1')")
    plan = LearningPlan("learner-1", ("objective-1",), ("objective-1",), PresentationProfile.for_age(8), "plan-1")
    session = LearningSession.start(session_id="session-1", plan=plan)
    from math_tutor.infrastructure.persistence.repositories import _dump
    connection.execute("INSERT INTO learning_plans(plan_id,version,learner_id,plan_json,policy_version) VALUES(?,?,?,?,?)", ("plan-1", 1, "learner-1", _dump(plan), "policy-v1"))
    connection.execute("INSERT INTO learning_sessions(session_id,learner_id,plan_id,plan_version,session_json,version,profile_version) VALUES(?,?,?,?,?,?,?)", ("session-1", "learner-1", "plan-1", 1, _dump(session), 1, 1))
    activity = Activity(
        "template-1", "objective-1", 1, "2 + 3", {"a": 2, "b": 3},
        StructuredAnswer.evaluable(ExpectedAnswerKind.INTEGER, {"answer": 5}), (), (),
    )
    connection.execute(
        "INSERT INTO activities(session_id,activity_id,activity_json) VALUES(?,?,?)",
        ("session-1", "activity-1", _dump(activity)),
    )
    observation = Observation(
        "observation-1", "learner-1", "session-1", "objective-1", "activity-1",
        ObservationOutcome.CORRECT, .95, 0, TranscriptionReliabilityPolicy(.7), "5",
    )
    connection.execute(
        "INSERT INTO observations(observation_id,session_id,learner_id,objective_id,observation_json) VALUES(?,?,?,?,?)",
        ("observation-1", "session-1", "learner-1", "objective-1", _dump(observation)),
    )
    connection.execute(
        "INSERT INTO evidence_records(evidence_id,observation_id,learner_id,objective_id,reason_for_retention) VALUES(?,?,?,?,?)",
        ("evidence-1", "observation-1", "learner-1", "objective-1", "migration-test"),
    )
    estimate = SkillEstimate("learner-1", "objective-1", CompetencyState.NOT_OBSERVED)
    connection.execute(
        "INSERT INTO skill_estimates(learner_id,objective_id,estimate_json,version) VALUES(?,?,?,?)",
        ("learner-1", "objective-1", _dump(estimate), 1),
    )
    proposal = ProposedProfileChange(
        "learner-1", "objective-1", CompetencyState.NOT_OBSERVED, CompetencyState.EXPLORING,
        ("evidence-1",), ("observation-1",), 1, "policy-v1",
    )
    connection.execute(
        "INSERT INTO profile_change_proposals(learner_id,objective_id,proposal_json,estimate_version,policy_version) VALUES(?,?,?,?,?)",
        ("learner-1", "objective-1", _dump(proposal), 1, "policy-v1"),
    )
    connection.commit()
    connection.close()

    migrate(database)

    repo = SQLiteTutoringRepository(database)
    assert repo.load_learner("learner-1").curriculum_snapshot == "old"
    assert repo.load_state("session-1").session == session
    assert repo.load_activity("session-1", "activity-1") == activity
    assert repo.load_profile_change_proposals("learner-1", "objective-1") == (proposal,)
    decision = repo.commit_once(MutationBatch(
        "post-upgrade", "fingerprint", "session-1", 1, 1,
        CommandResult("post-upgrade", CommandStatus.APPLIED, "stored"),
        events=(TutoringEvent("resumed", "session-1", "objective-1", "activity-1"),),
    ))
    assert decision.outcome.value == "applied"
    assert repo.load_command_result("post-upgrade").result.reason == "stored"
    with sqlite3.connect(database) as upgraded:
        assert [row[0] for row in upgraded.execute("SELECT version FROM schema_migrations ORDER BY version")] == [1, 2]
        assert "session_id" in {row[1] for row in upgraded.execute("PRAGMA table_info(profile_change_proposals)")}
        assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []
