from dataclasses import replace
from pathlib import Path
import shutil

from math_tutor.application.ports import ActivityProgress, CommitOutcome, MutationBatch, RegulationMutation, RegulationOutcome, StoredActivity
from math_tutor.application.results import CommandResult, CommandStatus
from math_tutor.application.service import CommitRegulation, RecordAnswer, EndSession, SupportLearner, TutoringService
from math_tutor.application.session_runtime import SessionRuntime
from math_tutor.domain.activities import Activity, StructuredAnswer
from math_tutor.domain.evidence import TranscriptionReliabilityPolicy
from math_tutor.domain.learning import AssistanceThreshold, CompetencyState, LearningPlan, LearningSession, PresentationProfile, ProgressionPolicy, SkillEstimate
from math_tutor.domain.regulation import ConfidenceBand, ConversationalSignal, ExecutedRegulationAction, PedagogicalStrategy
from math_tutor.domain.templates import ExpectedAnswerKind
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs
from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository

ROOT = Path(__file__).parents[3]


def _setup(tmp_path):
    database = tmp_path / "regulation-events.db"
    migrate(database)
    repo = SQLiteTutoringRepository(database)
    plan = LearningPlan("learner", ("units-tens",), ("units-tens",), PresentationProfile.for_age(8), "plan")
    repo.save_learner("learner", curriculum_snapshot="snapshot", curriculum_version="v1")
    repo.save_plan(plan, policy_version="v1")
    repo.save_session(LearningSession.start(session_id="session", plan=plan), profile_version=1)
    repo.save_estimate(SkillEstimate("learner", "units-tens", CompetencyState.NOT_OBSERVED))
    activity = Activity("template", "units-tens", 1, "¿Cuántas unidades?", {"number": 12}, StructuredAnswer.evaluable(ExpectedAnswerKind.INTEGER, {"answer": 2}), (), ("hint-1",))
    batch = MutationBatch("bootstrap", "bootstrap", "session", 1, 1, CommandResult("bootstrap", CommandStatus.APPLIED, "applied"), activities=(StoredActivity("activity", activity),), activity_progress=(ActivityProgress("activity", 0, 0, 1),), expected_absent_activity_ids=("activity",))
    assert repo.commit_once(batch).outcome is CommitOutcome.APPLIED
    runtime = SessionRuntime()
    catalog = load_curriculum_catalogs(ROOT / "src/math_tutor/curricula/primary-math-v1.yaml", ROOT / "src/math_tutor/curricula/activity-templates-v1.yaml")[1]
    thresholds = tuple(AssistanceThreshold(state, 3) for state in (
        CompetencyState.EXPLORING, CompetencyState.WITH_INTENSIVE_HELP,
        CompetencyState.WITH_LIGHT_HELP, CompetencyState.INDEPENDENT,
        CompetencyState.GENERALIZED,
    ))
    service = TutoringService(repo, runtime, catalog, TranscriptionReliabilityPolicy(.75), ProgressionPolicy(thresholds), reviewed_hint_texts={"hint-1": "Mira las unidades."})
    return database, repo, runtime, service


def _regulate(repo, runtime, service, number, signal=ConversationalSignal.CONFUSED):
    state = repo.load_state("session")
    generation = runtime.start_generation("session")
    strategy = PedagogicalStrategy.VALIDATE_EMOTION if signal is ConversationalSignal.FRUSTRATED else PedagogicalStrategy.SIMPLIFY_LANGUAGE
    return service.commit_regulation(CommitRegulation(command_id=f"regulate-{number}", session_id="session", expected_session_version=state.session.version, expected_profile_version=1, generation_id=generation.generation_id, turn_id=f"turn-{number}", activity_id="activity", expected_regulation_revision=state.regulation_revision, signal=signal, confidence_band=ConfidenceBand.HIGH, strategy=strategy, max_consecutive_regulation_turns=4, presentation=("short",), adaptations=()))


def _rows(repo):
    with repo._connect() as db:
        return [tuple(row) for row in db.execute("SELECT event_id,turn_id,signal,strategy,ordinal,outcome FROM regulation_events ORDER BY ordinal")]


def test_low_priority_turns_promote_exactly_on_second_and_advance_on_third(tmp_path):
    _, repo, runtime, service = _setup(tmp_path)
    _regulate(repo, runtime, service, 1)
    assert _rows(repo) == []
    assert repo.load_state("session").pending_regulation_event.event_id == "regulation-session-turn-1"
    _regulate(repo, runtime, service, 2)
    assert _rows(repo) == [
        ("regulation-session-turn-1", "turn-1", "confused", "simplify-language", 1, "repeated_difficulty"),
        ("regulation-session-turn-2", "turn-2", "confused", "simplify-language", 2, "unknown"),
    ]
    _regulate(repo, runtime, service, 3)
    assert _rows(repo)[1:] == [
        ("regulation-session-turn-2", "turn-2", "confused", "simplify-language", 2, "repeated_difficulty"),
        ("regulation-session-turn-3", "turn-3", "confused", "simplify-language", 3, "unknown"),
    ]


def test_high_priority_materializes_immediately_and_replay_is_exactly_idempotent(tmp_path):
    _, repo, runtime, service = _setup(tmp_path)
    generation = runtime.start_generation("session")
    command = CommitRegulation(command_id="regulate-1", session_id="session", expected_session_version=1, expected_profile_version=1, generation_id=generation.generation_id, turn_id="turn-1", activity_id="activity", expected_regulation_revision=0, signal=ConversationalSignal.FRUSTRATED, confidence_band=ConfidenceBand.HIGH, strategy=PedagogicalStrategy.VALIDATE_EMOTION, max_consecutive_regulation_turns=4, presentation=("short",), adaptations=())
    result = service.commit_regulation(command)
    before = (repo.load_state("session"), _rows(repo))
    replay = service.commit_regulation(command)
    assert replay.replayed and replay.payload == result.payload
    assert (repo.load_state("session"), _rows(repo)) == before
    assert _rows(repo)[0][-1] == "unknown"


def test_evaluable_answer_closes_open_event_and_resets_count_not_ordinal(tmp_path):
    _, repo, runtime, service = _setup(tmp_path)
    _regulate(repo, runtime, service, 1, ConversationalSignal.FRUSTRATED)
    state = repo.load_state("session"); generation = runtime.start_generation("session")
    result = service.record_answer(RecordAnswer(command_id="answer", session_id="session", expected_session_version=state.session.version, expected_profile_version=1, generation_id=generation.generation_id, activity_id="activity", answer=StructuredAnswer.evaluable(ExpectedAnswerKind.INTEGER, {"answer": 2}), response_text=None, stt_confidence=.9, assistance_level=0, observation_id="obs", evidence_id=None, retain_evidence=False, reason_for_retention=None))
    assert result.status is CommandStatus.APPLIED
    state = repo.load_state("session")
    assert (state.consecutive_regulation_turns, state.activity_sequence, state.pending_regulation_event) == (0, 1, None)
    assert _rows(repo)[0][-1] == "answered"
    _regulate(repo, runtime, service, 2)
    assert repo.load_state("session").pending_regulation_event.ordinal == 2


def test_regulation_storage_contains_only_closed_structured_fields(tmp_path, caplog):
    _, repo, runtime, service = _setup(tmp_path)
    _regulate(repo, runtime, service, 1, ConversationalSignal.FRUSTRATED)
    with repo._connect() as db:
        event_columns = {row[1] for row in db.execute("PRAGMA table_info(regulation_events)")}
        state_columns = {row[1] for row in db.execute("PRAGMA table_info(regulation_state)")}
        stored = " ".join(str(value) for table in (
            "regulation_events", "regulation_state", "processed_commands",
            "learner_support_receipts",
        ) for row in db.execute(f"SELECT * FROM {table}") for value in row)
    forbidden = {"transcript", "rationale", "speech", "diagnosis", "profile"}
    assert not forbidden.intersection(event_columns | state_columns)
    assert "¿Cuántas unidades?" not in stored
    assert all(word not in stored.lower() for word in ("transcript", "rationale", "diagnosis"))
    assert "¿Cuántas unidades?" not in caplog.text


def test_deterministic_support_uses_same_pending_and_promotion_state_machine(tmp_path):
    _, repo, runtime, service = _setup(tmp_path)
    for number in (1, 2):
        state = repo.load_state("session"); generation = runtime.start_generation("session")
        result = service.support_learner(SupportLearner(
            command_id=f"support-{number}", session_id="session",
            expected_session_version=state.session.version, expected_profile_version=1,
            generation_id=generation.generation_id, turn_id=f"help-{number}",
            activity_id="activity",
        ))
        assert result.status is CommandStatus.APPLIED
    assert [(row[1], row[4], row[5]) for row in _rows(repo)] == [
        ("help-1", 1, "repeated_difficulty"), ("help-2", 2, "unknown")
    ]


def test_explicit_stop_promotes_single_low_priority_pending_as_stopped(tmp_path):
    _, repo, runtime, service = _setup(tmp_path)
    _regulate(repo, runtime, service, 1)
    state = repo.load_state("session")
    result = service.stop_now(EndSession(
        command_id="stop", session_id="session",
        expected_session_version=state.session.version, expected_profile_version=1,
        generation_id="stopped-generation", reason="stop-requested",
    ))
    assert result.status is CommandStatus.APPLIED
    assert _rows(repo)[0][-1] == "stopped"
    state = repo.load_state("session")
    assert state.pending_regulation_event is None


def test_non_evaluable_answer_does_not_close_or_reset_pending_regulation(tmp_path):
    _, repo, runtime, service = _setup(tmp_path)
    _regulate(repo, runtime, service, 1, ConversationalSignal.FRUSTRATED)
    before = repo.load_state("session")
    generation = runtime.start_generation("session")
    result = service.record_answer(RecordAnswer(
        command_id="ambiguous", session_id="session",
        expected_session_version=before.session.version, expected_profile_version=1,
        generation_id=generation.generation_id, activity_id="activity",
        answer=StructuredAnswer.not_evaluable(), response_text=None,
        stt_confidence=.9, assistance_level=0, observation_id="obs-ambiguous",
        evidence_id=None, retain_evidence=False, reason_for_retention=None,
    ))
    assert result.status is CommandStatus.APPLIED
    after = repo.load_state("session")
    assert (after.regulation_revision, after.consecutive_regulation_turns,
            after.activity_sequence, after.pending_regulation_event) == (
                before.regulation_revision, before.consecutive_regulation_turns,
                before.activity_sequence, before.pending_regulation_event,
            )
    assert repo.load_regulation_events("session")[0].outcome is RegulationOutcome.UNKNOWN


def test_new_activity_state_transition_closes_unknown_and_resets_activity_sequence(tmp_path):
    _, repo, runtime, service = _setup(tmp_path)
    _regulate(repo, runtime, service, 1)
    state = repo.load_state("session")
    decision = repo.commit_once(MutationBatch(
        "new-activity", "new-activity", "session", state.session.version, 1,
        CommandResult("new-activity", CommandStatus.APPLIED, "applied"),
        expected_regulation_revision=state.regulation_revision,
        regulation_mutation=RegulationMutation(
            state.regulation_revision + 1, 0, 0,
            ExecutedRegulationAction.CAP_CHOICE,
            close_outcome=RegulationOutcome.UNKNOWN,
        ),
    ))
    assert decision.outcome is CommitOutcome.APPLIED
    state = repo.load_state("session")
    assert (state.consecutive_regulation_turns, state.activity_sequence,
            state.pending_regulation_event) == (0, 0, None)
    assert repo.load_regulation_events("session")[0].outcome is RegulationOutcome.UNKNOWN


def test_upgrade_preserves_migration_0019_support_receipt_without_backfill(tmp_path):
    database = tmp_path / "legacy-support.db"
    source = ROOT / "src/math_tutor/infrastructure/persistence/migrations"
    legacy = tmp_path / "legacy-migrations"; legacy.mkdir()
    for migration in source.glob("*.sql"):
        if int(migration.name.split("_", 1)[0]) <= 19:
            shutil.copy(migration, legacy / migration.name)
    migrate(database, migration_dir=legacy)
    repo = SQLiteTutoringRepository(database)
    plan = LearningPlan("learner", ("units-tens",), ("units-tens",), PresentationProfile.for_age(8), "plan")
    repo.save_learner("learner", curriculum_snapshot="snapshot", curriculum_version="v1")
    repo.save_plan(plan, policy_version="v1")
    repo.save_session(LearningSession.start(session_id="session", plan=plan), profile_version=1)
    activity = Activity("template", "units-tens", 1, "Histórico", {"number": 12}, StructuredAnswer.evaluable(ExpectedAnswerKind.INTEGER, {"answer": 2}), (), ())
    from math_tutor.infrastructure.persistence.repositories import _dump
    with repo._connect() as db:
        db.execute("INSERT INTO activities(session_id,activity_id,objective_id,activity_json) VALUES(?,?,?,?)", ("session", "activity", "units-tens", _dump(activity)))
        db.execute("INSERT INTO learner_support_receipts(session_id,turn_id,activity_id,action,speech) VALUES(?,?,?,?,?)", ("session", "old-turn", "activity", "repeat", "Texto canónico histórico."))
    migrate(database)
    runtime = SessionRuntime(); service = _setup(tmp_path / "fresh")[3]
    service._repository = SQLiteTutoringRepository(database)
    result = service.support_learner(SupportLearner(
        command_id="old-support", session_id="session", expected_session_version=1,
        expected_profile_version=1, generation_id="irrelevant", turn_id="old-turn",
        activity_id="activity",
    ))
    assert result.replayed and result.payload.speech == "Texto canónico histórico."
    state = service._repository.load_state("session")
    assert (state.regulation_revision, state.activity_sequence,
            state.pending_regulation_event) == (0, 0, None)
    assert service._repository.load_regulation_events("session") == ()
