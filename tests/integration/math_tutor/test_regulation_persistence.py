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
from math_tutor.domain.regulation import ConfidenceBand, ConversationalSignal, ExecutedRegulationAction, PedagogicalStrategy, RegulationPolicy
from math_tutor.domain.templates import ExpectedAnswerKind
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs
from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository, _dump

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
    return service.commit_regulation(CommitRegulation(command_id=f"regulate-{number}", session_id="session", expected_session_version=state.session.version, expected_profile_version=1, generation_id=generation.generation_id, turn_id=f"turn-{number}", activity_id="activity", expected_regulation_revision=state.regulation_revision, signal=signal, confidence_band=ConfidenceBand.HIGH, strategy=strategy, regulation_policy=RegulationPolicy(tuple(PedagogicalStrategy), 4), presentation=("short",), adaptations=()))


def _rows(repo):
    with repo._connect() as db:
        return [tuple(row) for row in db.execute("SELECT event_id,turn_id,signal,strategy,ordinal,outcome FROM regulation_events ORDER BY ordinal")]


def _durable_regulation_snapshot(repo):
    state = repo.load_state("session")
    progress = state.progress_for("activity")
    with repo._connect() as db:
        commands = db.execute("SELECT COUNT(*) FROM processed_commands").fetchone()[0]
        receipts = db.execute("SELECT COUNT(*) FROM learner_support_receipts").fetchone()[0]
    return (
        state.regulation_revision, state.consecutive_regulation_turns,
        state.activity_sequence, state.pending_regulation_event,
        progress.hints_used, progress.version, tuple(_rows(repo)), commands, receipts,
    )


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
    command = CommitRegulation(command_id="regulate-1", session_id="session", expected_session_version=1, expected_profile_version=1, generation_id=generation.generation_id, turn_id="turn-1", activity_id="activity", expected_regulation_revision=0, signal=ConversationalSignal.FRUSTRATED, confidence_band=ConfidenceBand.HIGH, strategy=PedagogicalStrategy.VALIDATE_EMOTION, regulation_policy=RegulationPolicy(tuple(PedagogicalStrategy), 4), presentation=("short",), adaptations=())
    result = service.commit_regulation(command)
    before = (repo.load_state("session"), _rows(repo))
    replay = service.commit_regulation(command)
    assert replay.replayed and replay.payload == result.payload
    assert (repo.load_state("session"), _rows(repo)) == before
    assert _rows(repo)[0][-1] == "unknown"


def test_llm_regulation_replays_by_turn_after_repository_and_engine_reopen(tmp_path):
    database, repo, runtime, service = _setup(tmp_path)
    first = _regulate(repo, runtime, service, 1, ConversationalSignal.FRUSTRATED)
    before = _durable_regulation_snapshot(repo)

    reopened_repo = SQLiteTutoringRepository(database)
    reopened_runtime = SessionRuntime()
    reopened_service = _setup(tmp_path / "service-fixture")[3]
    reopened_service._repository = reopened_repo
    reopened_service._runtime = reopened_runtime
    replay = reopened_service.commit_regulation(CommitRegulation(
        command_id="different-ephemeral-command", session_id="session",
        expected_session_version=1, expected_profile_version=1,
        generation_id="generation-that-does-not-exist", turn_id="turn-1",
        activity_id="activity", expected_regulation_revision=0,
        signal=ConversationalSignal.CONFUSED, confidence_band=ConfidenceBand.MEDIUM,
        strategy=PedagogicalStrategy.SIMPLIFY_LANGUAGE,
        regulation_policy=RegulationPolicy(tuple(PedagogicalStrategy), 4),
        presentation=("short",), adaptations=(),
    ))

    assert replay.status is CommandStatus.APPLIED and replay.replayed
    assert replay.payload == first.payload
    assert _durable_regulation_snapshot(reopened_repo) == before


def test_turn_receipt_does_not_mask_a_same_command_id_collision(tmp_path):
    _, repo, runtime, service = _setup(tmp_path)
    first = _regulate(repo, runtime, service, 1, ConversationalSignal.FRUSTRATED)
    before = _durable_regulation_snapshot(repo)
    collision = service.commit_regulation(CommitRegulation(
        command_id="regulate-1", session_id="session",
        expected_session_version=1, expected_profile_version=1,
        generation_id="different-generation", turn_id="turn-1",
        activity_id="activity", expected_regulation_revision=0,
        signal=ConversationalSignal.CONFUSED, confidence_band=ConfidenceBand.HIGH,
        strategy=PedagogicalStrategy.SIMPLIFY_LANGUAGE,
        regulation_policy=RegulationPolicy(tuple(PedagogicalStrategy), 4),
        presentation=("short",), adaptations=(),
    ))
    assert first.status is CommandStatus.APPLIED
    assert (collision.status, collision.reason) == (
        CommandStatus.REJECTED, "command-id-collision",
    )
    assert _durable_regulation_snapshot(repo) == before


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
            regulation_policy=RegulationPolicy(tuple(PedagogicalStrategy), 4),
            presentation=("short",), adaptations=(),
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


def test_end_session_closes_materialized_event_as_stopped(tmp_path):
    _, repo, runtime, service = _setup(tmp_path)
    _regulate(repo, runtime, service, 1, ConversationalSignal.FRUSTRATED)
    state = repo.load_state("session"); generation = runtime.start_generation("session")
    result = service.end_session(EndSession(
        command_id="end", session_id="session",
        expected_session_version=state.session.version, expected_profile_version=1,
        generation_id=generation.generation_id, reason="stop-requested",
    ))
    assert result.status is CommandStatus.APPLIED
    assert repo.load_regulation_events("session")[0].outcome is RegulationOutcome.STOPPED


def test_stale_revision_and_stale_generation_create_no_event_or_receipt(tmp_path):
    _, repo, runtime, service = _setup(tmp_path)
    old_generation = runtime.start_generation("session")
    runtime.start_generation("session")
    stale_generation = service.commit_regulation(CommitRegulation(
        command_id="stale-generation", session_id="session",
        expected_session_version=1, expected_profile_version=1,
        generation_id=old_generation.generation_id, turn_id="stale-generation",
        activity_id="activity", expected_regulation_revision=0,
        signal=ConversationalSignal.CONFUSED, confidence_band=ConfidenceBand.HIGH,
        strategy=PedagogicalStrategy.SIMPLIFY_LANGUAGE,
        regulation_policy=RegulationPolicy(tuple(PedagogicalStrategy), 4), presentation=("short",), adaptations=(),
    ))
    current = runtime.start_generation("session")
    stale_revision = service.commit_regulation(CommitRegulation(
        command_id="stale-revision", session_id="session",
        expected_session_version=1, expected_profile_version=1,
        generation_id=current.generation_id, turn_id="stale-revision",
        activity_id="activity", expected_regulation_revision=1,
        signal=ConversationalSignal.CONFUSED, confidence_band=ConfidenceBand.HIGH,
        strategy=PedagogicalStrategy.SIMPLIFY_LANGUAGE,
        regulation_policy=RegulationPolicy(tuple(PedagogicalStrategy), 4), presentation=("short",), adaptations=(),
    ))
    assert stale_generation.reason == "generation-not-active"
    assert stale_revision.reason == "stale-regulation-revision"
    assert repo.load_regulation_events("session") == ()
    assert repo.load_state("session").pending_regulation_event is None
    assert repo.load_command_result("stale-generation") is None
    assert repo.load_command_result("stale-revision") is None


def test_reopen_retains_open_event_with_unknown_outcome(tmp_path):
    database, repo, runtime, service = _setup(tmp_path)
    _regulate(repo, runtime, service, 1, ConversationalSignal.FRUSTRATED)
    reopened = SQLiteTutoringRepository(database)
    event = reopened.load_regulation_events("session")[0]
    assert event.outcome is RegulationOutcome.UNKNOWN
    assert reopened.load_state("session").pending_regulation_event.event_id == event.event_id


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
    with repo._connect() as db:
        db.execute("INSERT INTO activities(session_id,activity_id,objective_id,activity_json) VALUES(?,?,?,?)", ("session", "activity", "units-tens", _dump(activity)))
        db.execute("INSERT INTO learner_support_receipts(session_id,turn_id,activity_id,action,speech) VALUES(?,?,?,?,?)", ("session", "old-turn", "activity", "repeat", "Texto canónico histórico."))
    migrate(database)
    runtime = SessionRuntime(); service = _setup(tmp_path / "fresh")[3]
    service._repository = SQLiteTutoringRepository(database)
    service._runtime = runtime
    result = service.support_learner(SupportLearner(
        command_id="old-support", session_id="session", expected_session_version=1,
        expected_profile_version=1, generation_id="irrelevant", turn_id="old-turn",
        activity_id="activity",
        regulation_policy=RegulationPolicy(tuple(PedagogicalStrategy), 4),
        presentation=("short",), adaptations=(),
    ))
    assert result.replayed and result.payload.speech == "Texto canónico histórico."
    state = service._repository.load_state("session")
    assert (state.regulation_revision, state.activity_sequence,
            state.pending_regulation_event) == (0, 0, None)
    assert service._repository.load_regulation_events("session") == ()
    generation = runtime.start_generation("session")
    new_result = service.support_learner(SupportLearner(
        command_id="new-support", session_id="session", expected_session_version=1,
        expected_profile_version=1, generation_id=generation.generation_id,
        turn_id="new-turn", activity_id="activity",
        regulation_policy=RegulationPolicy(tuple(PedagogicalStrategy), 4),
        presentation=("short",), adaptations=(),
    ))
    assert new_result.status is CommandStatus.APPLIED
    state = service._repository.load_state("session")
    assert (state.regulation_revision, state.activity_sequence) == (1, 1)
    assert state.pending_regulation_event.turn_id == "new-turn"


def test_support_uses_authorised_order_and_therapist_cap_without_defaults(tmp_path):
    _, repo, runtime, service = _setup(tmp_path)
    policy = RegulationPolicy((
        PedagogicalStrategy.SIMPLIFY_LANGUAGE,
        PedagogicalStrategy.VALIDATE_EMOTION,
        PedagogicalStrategy.REDIRECT_GENTLY,
        PedagogicalStrategy.TAKE_SHORT_PAUSE,
    ), 1)
    for number in (1, 2):
        state = repo.load_state("session"); generation = runtime.start_generation("session")
        result = service.support_learner(SupportLearner(
            command_id=f"policy-help-{number}", session_id="session",
            expected_session_version=state.session.version, expected_profile_version=1,
            generation_id=generation.generation_id, turn_id=f"policy-help-{number}",
            activity_id="activity", regulation_policy=policy,
            presentation=("short",), adaptations=(),
        ))
        assert result.status is CommandStatus.APPLIED
        if number == 1:
            assert result.payload.speech.startswith("Vamos paso a paso")
            assert repo.load_state("session").pending_regulation_event.strategy is ExecutedRegulationAction.SIMPLIFY_LANGUAGE
        else:
            assert result.payload.speech == "¿Quieres continuar o hacer una pausa?"
    assert repo.load_state("session").activity_sequence == 2


def test_support_simplify_and_cap_replay_exactly_after_reopen(tmp_path):
    database, repo, runtime, service = _setup(tmp_path)
    policy = RegulationPolicy((
        PedagogicalStrategy.SIMPLIFY_LANGUAGE,
        PedagogicalStrategy.VALIDATE_EMOTION,
        PedagogicalStrategy.REDIRECT_GENTLY,
        PedagogicalStrategy.TAKE_SHORT_PAUSE,
    ), 1)
    originals = []
    for number in (1, 2):
        state = repo.load_state("session")
        generation = runtime.start_generation("session")
        originals.append(service.support_learner(SupportLearner(
            command_id=f"original-{number}", session_id="session",
            expected_session_version=state.session.version, expected_profile_version=1,
            generation_id=generation.generation_id, turn_id=f"help-{number}",
            activity_id="activity", regulation_policy=policy,
            presentation=("short",), adaptations=(),
        )))
    before = _durable_regulation_snapshot(repo)

    reopened_repo = SQLiteTutoringRepository(database)
    reopened_service = _setup(tmp_path / "reopened-service")[3]
    reopened_service._repository = reopened_repo
    reopened_service._runtime = SessionRuntime()
    for number, original in enumerate(originals, 1):
        replay = reopened_service.support_learner(SupportLearner(
            command_id=f"replay-{number}", session_id="session",
            expected_session_version=0, expected_profile_version=0,
            generation_id="not-active", turn_id=f"help-{number}",
            activity_id="different-activity", regulation_policy=object(),
            presentation=("invalid",), adaptations=("invalid",),
        ))
        assert replay.status is CommandStatus.APPLIED and replay.replayed
        assert replay.payload == original.payload

    assert originals[0].payload.action == "simplify-language"
    assert originals[1].payload.action == "cap-choice"
    assert _durable_regulation_snapshot(reopened_repo) == before


def test_support_falls_back_to_authorised_repeat_when_hint_and_simplify_are_disabled(tmp_path):
    _, repo, runtime, service = _setup(tmp_path)
    policy = RegulationPolicy((
        PedagogicalStrategy.REPEAT_INSTRUCTION,
        PedagogicalStrategy.VALIDATE_EMOTION,
        PedagogicalStrategy.REDIRECT_GENTLY,
        PedagogicalStrategy.TAKE_SHORT_PAUSE,
    ), 4)
    generation = runtime.start_generation("session")
    result = service.support_learner(SupportLearner(
        command_id="repeat-only", session_id="session", expected_session_version=1,
        expected_profile_version=1, generation_id=generation.generation_id,
        turn_id="repeat-only", activity_id="activity", regulation_policy=policy,
        presentation=("short",), adaptations=(),
    ))
    assert result.payload.speech == "¿Cuántas unidades?"
    assert result.payload.action == "repeat"
    assert repo.load_state("session").pending_regulation_event.strategy is ExecutedRegulationAction.REPEAT_INSTRUCTION


def _set_hint_exhausted(repo):
    state = repo.load_state("session")
    progress = replace(state.progress_for("activity"), hints_used=1, version=2)
    with repo._connect() as db:
        db.execute(
            "UPDATE activity_progress SET progress_json=?,version=2 WHERE session_id='session' AND activity_id='activity'",
            (_dump(progress),),
        )


def test_exhausted_hint_uses_authorised_simplify_fallback(tmp_path):
    _, repo, runtime, service = _setup(tmp_path); _set_hint_exhausted(repo)
    generation = runtime.start_generation("session")
    result = service.support_learner(SupportLearner(
        command_id="exhausted", session_id="session", expected_session_version=1,
        expected_profile_version=1, generation_id=generation.generation_id,
        turn_id="exhausted", activity_id="activity",
        regulation_policy=RegulationPolicy(tuple(PedagogicalStrategy), 4),
        presentation=("short",), adaptations=(),
    ))
    assert result.status is CommandStatus.APPLIED
    assert result.payload.action == "simplify-language"
    assert repo.load_state("session").pending_regulation_event.strategy is ExecutedRegulationAction.SIMPLIFY_LANGUAGE


def test_missing_canonical_hint_uses_authorised_repeat_fallback(tmp_path):
    _, repo, runtime, service = _setup(tmp_path)
    service._reviewed_hint_texts = {}
    policy = RegulationPolicy((
        PedagogicalStrategy.GIVE_ORDERED_HINT,
        PedagogicalStrategy.REPEAT_INSTRUCTION,
        PedagogicalStrategy.VALIDATE_EMOTION,
        PedagogicalStrategy.REDIRECT_GENTLY,
        PedagogicalStrategy.TAKE_SHORT_PAUSE,
    ), 4)
    generation = runtime.start_generation("session")
    result = service.support_learner(SupportLearner(
        command_id="missing-hint", session_id="session", expected_session_version=1,
        expected_profile_version=1, generation_id=generation.generation_id,
        turn_id="missing-hint", activity_id="activity", regulation_policy=policy,
        presentation=("short",), adaptations=(),
    ))
    assert result.status is CommandStatus.APPLIED
    assert result.payload.action == "repeat"
    assert repo.load_state("session").pending_regulation_event.strategy is ExecutedRegulationAction.REPEAT_INSTRUCTION


def test_impossible_corrupt_help_policy_fails_closed_without_mutation_or_speech(tmp_path):
    _, repo, runtime, service = _setup(tmp_path); _set_hint_exhausted(repo)
    corrupt = object.__new__(RegulationPolicy)
    object.__setattr__(corrupt, "allowed_strategies", (PedagogicalStrategy.GIVE_ORDERED_HINT,))
    object.__setattr__(corrupt, "max_consecutive_regulation_turns", 4)
    before = repo.load_state("session")
    generation = runtime.start_generation("session")
    result = service.support_learner(SupportLearner(
        command_id="corrupt-policy", session_id="session", expected_session_version=1,
        expected_profile_version=1, generation_id=generation.generation_id,
        turn_id="corrupt-policy", activity_id="activity", regulation_policy=corrupt,
        presentation=("short",), adaptations=(),
    ))
    assert (result.status, result.reason, result.payload) == (
        CommandStatus.REJECTED, "no-authorised-help-fallback", None,
    )
    assert repo.load_state("session") == before
    assert repo.load_command_result("corrupt-policy") is None


def _commit_llm_help(repo, runtime, service, *, turn_id, policy):
    state = repo.load_state("session"); generation = runtime.start_generation("session")
    return service.commit_regulation(CommitRegulation(
        command_id=f"llm-{turn_id}", session_id="session",
        expected_session_version=state.session.version, expected_profile_version=1,
        generation_id=generation.generation_id, turn_id=turn_id,
        activity_id="activity", expected_regulation_revision=state.regulation_revision,
        signal=ConversationalSignal.REQUESTING_HELP,
        confidence_band=ConfidenceBand.HIGH,
        strategy=PedagogicalStrategy.GIVE_ORDERED_HINT,
        regulation_policy=policy, presentation=("short",), adaptations=(),
    ))


def test_llm_exhausted_hint_persists_authorised_repeat_not_simplify(tmp_path):
    _, repo, runtime, service = _setup(tmp_path); _set_hint_exhausted(repo)
    policy = RegulationPolicy((
        PedagogicalStrategy.GIVE_ORDERED_HINT,
        PedagogicalStrategy.REPEAT_INSTRUCTION,
        PedagogicalStrategy.VALIDATE_EMOTION,
        PedagogicalStrategy.REDIRECT_GENTLY,
        PedagogicalStrategy.TAKE_SHORT_PAUSE,
    ), 4)
    first = _commit_llm_help(repo, runtime, service, turn_id="fallback-1", policy=policy)
    second = _commit_llm_help(repo, runtime, service, turn_id="fallback-2", policy=policy)
    assert first.payload.executed_action is ExecutedRegulationAction.REPEAT_INSTRUCTION
    assert second.payload.executed_action is ExecutedRegulationAction.REPEAT_INSTRUCTION
    events = repo.load_regulation_events("session")
    assert [event.strategy for event in events] == [
        ExecutedRegulationAction.REPEAT_INSTRUCTION,
        ExecutedRegulationAction.REPEAT_INSTRUCTION,
    ]


def test_llm_and_deterministic_help_share_hint_selection_and_canonical_speech(tmp_path):
    _, support_repo, support_runtime, support_service = _setup(tmp_path / "support")
    _, llm_repo, llm_runtime, llm_service = _setup(tmp_path / "llm")
    policy = RegulationPolicy(tuple(PedagogicalStrategy), 4)
    generation = support_runtime.start_generation("session")
    support = support_service.support_learner(SupportLearner(
        command_id="support-parity", session_id="session", expected_session_version=1,
        expected_profile_version=1, generation_id=generation.generation_id,
        turn_id="parity", activity_id="activity", regulation_policy=policy,
        presentation=("short",), adaptations=(),
    ))
    llm = _commit_llm_help(
        llm_repo, llm_runtime, llm_service, turn_id="parity", policy=policy,
    )
    assert support.payload.speech == llm.payload.speech
    assert support_repo.load_state("session").progress_for("activity").hints_used == 1
    assert llm_repo.load_state("session").progress_for("activity").hints_used == 1
    assert support_repo.load_state("session").pending_regulation_event.strategy is ExecutedRegulationAction.GIVE_ORDERED_HINT
    assert llm_repo.load_state("session").pending_regulation_event.strategy is ExecutedRegulationAction.GIVE_ORDERED_HINT
