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
from math_tutor.application.ports import ActivityProgress, MutationBatch, TutoringEvent
from math_tutor.application.results import CommandResult, CommandStatus


LEGACY_PLAN_JSON = '{"$dataclass":"math_tutor.domain.learning:LearningPlan","fields":{"active_objective_ids":{"$tuple":["objective-1"]},"authorised_objective_ids":{"$tuple":["objective-1"]},"learner_id":"learner-1","plan_id":"plan-1","presentation":{"$dataclass":"math_tutor.domain.learning:PresentationProfile","fields":{"instruction_length":"short","language_style":"concrete-and-playful","learner_age":8}},"version":1}}'
LEGACY_SESSION_JSON = '{"$dataclass":"math_tutor.domain.learning:LearningSession","fields":{"active_objective_ids":{"$tuple":["objective-1"]},"authorised_objective_ids":{"$tuple":["objective-1"]},"end_reason":null,"ended":false,"learner_id":"learner-1","plan_id":"plan-1","plan_version":1,"session_id":"session-1","stop_requested":false,"version":1}}'
LEGACY_ACTIVITY_JSON = '{"$dataclass":"math_tutor.domain.activities:Activity","fields":{"difficulty":1,"error_pattern_ids":{"$tuple":[]},"expected_answer":{"$dataclass":"math_tutor.domain.activities:StructuredAnswer","fields":{"kind":{"$enum":"math_tutor.domain.templates:ExpectedAnswerKind","value":"integer"},"status":{"$enum":"math_tutor.domain.activities:AnswerInputStatus","value":"evaluable"},"values":{"$mapping":[["answer",5]]}}},"hint_ids":{"$tuple":[]},"objective_id":"objective-1","parameters":{"$mapping":[["a",2],["b",3]]},"prompt_es":"2 + 3","template_id":"template-1"}}'
LEGACY_OBSERVATION_JSON = '{"$dataclass":"math_tutor.domain.evidence:Observation","fields":{"activity_id":"activity-1","assistance_level":0,"learner_id":"learner-1","objective_id":"objective-1","observation_id":"observation-1","outcome":{"$enum":"math_tutor.domain.evidence:ObservationOutcome","value":"correct"},"response_text":"cinco","session_id":"session-1","stt_confidence":0.95,"transcription_policy":{"$dataclass":"math_tutor.domain.evidence:TranscriptionReliabilityPolicy","fields":{"min_confidence":0.7}}}}'
LEGACY_PROGRESS_JSON = '{"$dataclass":"math_tutor.application.ports:ActivityProgress","fields":{"activity_id":"activity-1","attempts_used":1,"consecutive_correct":1,"consecutive_incorrect":0,"difficulty":1,"hints_used":0,"version":1}}'
LEGACY_ESTIMATE_JSON = '{"$dataclass":"math_tutor.domain.learning:SkillEstimate","fields":{"learner_id":"learner-1","objective_id":"objective-1","state":{"$enum":"math_tutor.domain.learning:CompetencyState","value":"not-observed"},"supporting_evidence_ids":{"$tuple":[]},"supporting_observation_ids":{"$tuple":[]},"version":1}}'
LEGACY_PROPOSAL_JSON = '{"$dataclass":"math_tutor.domain.learning:ProposedProfileChange","fields":{"evidence_ids":{"$tuple":["evidence-1"]},"from_state":{"$enum":"math_tutor.domain.learning:CompetencyState","value":"not-observed"},"learner_id":"learner-1","objective_id":"objective-1","observation_ids":{"$tuple":["observation-1"]},"policy_version":"policy-v1","to_state":{"$enum":"math_tutor.domain.learning:CompetencyState","value":"exploring"},"estimate_version":1}}'
LEGACY_COMMAND_RESULT_JSON = '{"$dataclass":"math_tutor.application.results:CommandResult","fields":{"command_id":"legacy-command","payload":{"$mapping":[["spoken","Muy bien"],["score",5]]},"reason":"stored","replayed":false,"status":{"$enum":"math_tutor.application.results:CommandStatus","value":"applied"}}}'


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
        assert [row[0] for row in upgraded.execute("SELECT version FROM schema_migrations ORDER BY version")] == [1, 2, 3]
        assert "session_id" in {row[1] for row in upgraded.execute("PRAGMA table_info(profile_change_proposals)")}
        assert "source_session_id" in {row[1] for row in upgraded.execute("PRAGMA table_info(proposal_evidence)")}
        assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []


def test_frozen_legacy_codec_payloads_survive_upgrade_and_replay(tmp_path):
    """Payloads emitted by ccb4071 remain durable after the codec changed."""
    database = tmp_path / "legacy-codec.db"
    original_v1 = Path("src/math_tutor/infrastructure/persistence/migrations/0001_initial.sql").read_text()
    connection = sqlite3.connect(database)
    connection.executescript(original_v1)
    connection.execute("INSERT INTO schema_migrations(version) VALUES(1)")
    connection.execute("INSERT INTO learners(learner_id,curriculum_snapshot,curriculum_version) VALUES('learner-1','old','v1')")
    connection.execute("INSERT INTO learning_plans(plan_id,version,learner_id,plan_json,policy_version) VALUES('plan-1',1,'learner-1',?,'policy-v1')", (LEGACY_PLAN_JSON,))
    connection.execute("INSERT INTO learning_sessions(session_id,learner_id,plan_id,plan_version,session_json,version,profile_version) VALUES('session-1','learner-1','plan-1',1,?,1,1)", (LEGACY_SESSION_JSON,))
    connection.execute("INSERT INTO activities(session_id,activity_id,activity_json) VALUES('session-1','activity-1',?)", (LEGACY_ACTIVITY_JSON,))
    connection.execute("INSERT INTO activity_progress(session_id,activity_id,progress_json,version) VALUES('session-1','activity-1',?,1)", (LEGACY_PROGRESS_JSON,))
    connection.execute("INSERT INTO observations(observation_id,session_id,learner_id,objective_id,observation_json,version) VALUES('observation-1','session-1','learner-1','objective-1',?,1)", (LEGACY_OBSERVATION_JSON,))
    connection.execute("INSERT INTO evidence_records(evidence_id,observation_id,learner_id,objective_id,reason_for_retention) VALUES('evidence-1','observation-1','learner-1','objective-1','legacy')")
    connection.execute("INSERT INTO skill_estimates(learner_id,objective_id,estimate_json,version) VALUES('learner-1','objective-1',?,1)", (LEGACY_ESTIMATE_JSON,))
    connection.execute("INSERT INTO profile_change_proposals(learner_id,objective_id,proposal_json,estimate_version,policy_version) VALUES('learner-1','objective-1',?,1,'policy-v1')", (LEGACY_PROPOSAL_JSON,))
    connection.execute("INSERT INTO processed_commands(command_id,command_fingerprint,result_json) VALUES('legacy-command','legacy-fingerprint',?)", (LEGACY_COMMAND_RESULT_JSON,))
    connection.commit()
    connection.close()

    migrate(database)
    repo = SQLiteTutoringRepository(database)
    plan = LearningPlan("learner-1", ("objective-1",), ("objective-1",), PresentationProfile.for_age(8), "plan-1")
    expected_session = LearningSession.start(session_id="session-1", plan=plan)
    expected_activity = Activity(
        "template-1", "objective-1", 1, "2 + 3", {"a": 2, "b": 3},
        StructuredAnswer.evaluable(ExpectedAnswerKind.INTEGER, {"answer": 5}), (), (),
    )
    assert repo.load_state("session-1").session == expected_session
    assert repo.load_state("session-1").activity_progress == (
        ActivityProgress("activity-1", 1, 0, 1, consecutive_correct=1),
    )
    assert repo.load_activity("session-1", "activity-1") == expected_activity
    assert repo.load_observation("session-1", "observation-1").observation == Observation(
        "observation-1", "learner-1", "session-1", "objective-1",
        "activity-1", ObservationOutcome.CORRECT, .95, 0,
        TranscriptionReliabilityPolicy(.7), "cinco",
    )
    assert repo.load_estimate("learner-1", "objective-1") == SkillEstimate(
        "learner-1", "objective-1", CompetencyState.NOT_OBSERVED
    )
    assert repo.load_profile_change_proposals("learner-1", "objective-1") == (
        ProposedProfileChange(
            "learner-1", "objective-1", CompetencyState.NOT_OBSERVED,
            CompetencyState.EXPLORING, ("evidence-1",), ("observation-1",),
            1, "policy-v1",
        ),
    )
    replay = repo.commit_once(MutationBatch(
        "legacy-command", "legacy-fingerprint", "session-1", 1, 1,
        CommandResult("ignored", CommandStatus.REJECTED, "ignored"),
    ))
    assert replay.outcome.value == "replayed"
    assert replay.result == CommandResult(
        "legacy-command", CommandStatus.APPLIED, "stored",
        {"spoken": "Muy bien", "score": 5},
    )
