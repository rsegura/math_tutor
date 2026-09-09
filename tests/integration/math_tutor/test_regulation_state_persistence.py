from dataclasses import replace
from pathlib import Path

from math_tutor.application.ports import (
    ActivityProgress,
    CommitOutcome,
    MutationBatch,
    RegulationMutation,
    StoredActivity,
)
from math_tutor.application.regulation import ExecutedRegulationAction
from math_tutor.application.results import CommandResult, CommandStatus
from math_tutor.application.service import CommitRegulation, TutoringService
from math_tutor.application.session_runtime import SessionRuntime
from math_tutor.domain.activities import Activity, StructuredAnswer
from math_tutor.domain.evidence import TranscriptionReliabilityPolicy
from math_tutor.domain.learning import (
    AssistanceThreshold, CompetencyState, LearningPlan, LearningSession,
    PresentationProfile, ProgressionPolicy, SkillEstimate,
)
from math_tutor.domain.regulation import ConfidenceBand, ConversationalSignal, PedagogicalStrategy, RegulationPolicy
from math_tutor.domain.templates import ExpectedAnswerKind
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs
from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository


ROOT = Path(__file__).parents[3]


def setup_repository(tmp_path):
    path = tmp_path / "regulation.db"; migrate(path)
    repo = SQLiteTutoringRepository(path)
    plan = LearningPlan("learner", ("units-tens",), ("units-tens",), PresentationProfile.for_age(8), "plan")
    repo.save_learner("learner", curriculum_snapshot="snapshot", curriculum_version="v1")
    repo.save_plan(plan, policy_version="v1")
    repo.save_session(LearningSession.start(session_id="session", plan=plan), profile_version=1)
    repo.save_estimate(SkillEstimate("learner", "units-tens", CompetencyState.NOT_OBSERVED))
    activity = Activity(
        "template", "units-tens", 1, "¿Cuántas unidades?", {"number": 12},
        StructuredAnswer.evaluable(ExpectedAnswerKind.INTEGER, {"answer": 2}), (), ("hint-1",),
    )
    bootstrap = MutationBatch(
        "bootstrap", "bootstrap", "session", 1, 1,
        CommandResult("bootstrap", CommandStatus.APPLIED, "applied"),
        activities=(StoredActivity("activity", activity),),
        activity_progress=(ActivityProgress("activity", 0, 0, 1),),
        expected_absent_activity_ids=("activity",),
    )
    assert repo.commit_once(bootstrap).outcome is CommitOutcome.APPLIED
    return path, repo


def make_service(repo, runtime):
    catalog = load_curriculum_catalogs(
        ROOT / "src/math_tutor/curricula/primary-math-v1.yaml",
        ROOT / "src/math_tutor/curricula/activity-templates-v1.yaml",
    )[1]
    thresholds = tuple(AssistanceThreshold(state, 3) for state in (
        CompetencyState.EXPLORING, CompetencyState.WITH_INTENSIVE_HELP,
        CompetencyState.WITH_LIGHT_HELP, CompetencyState.INDEPENDENT,
        CompetencyState.GENERALIZED,
    ))
    return TutoringService(
        repo, runtime, catalog, TranscriptionReliabilityPolicy(.75),
        ProgressionPolicy(thresholds), reviewed_hint_texts={"hint-1": "Mira las unidades."},
    )


def regulate(service, runtime, *, number, revision, strategy=PedagogicalStrategy.SIMPLIFY_LANGUAGE):
    generation = runtime.start_generation("session")
    return service.commit_regulation(CommitRegulation(
        command_id=f"regulate-{number}", session_id="session",
        expected_session_version=1, expected_profile_version=1,
        generation_id=generation.generation_id, turn_id=f"turn-{number}",
        activity_id="activity", expected_regulation_revision=revision,
        signal=ConversationalSignal.CONFUSED, confidence_band=ConfidenceBand.HIGH,
        strategy=strategy, regulation_policy=RegulationPolicy(tuple(PedagogicalStrategy), 4),
        presentation=("short",), adaptations=(),
    ))


def test_regulation_state_accumulates_persists_across_restart_and_caps(tmp_path):
    path, repo = setup_repository(tmp_path); runtime = SessionRuntime(); service = make_service(repo, runtime)
    for number in range(1, 6):
        result = regulate(service, runtime, number=number, revision=number - 1)
        assert result.status is CommandStatus.APPLIED
    assert result.payload.executed_action is ExecutedRegulationAction.CAP_CHOICE
    state = SQLiteTutoringRepository(path).load_state("session")
    assert (state.regulation_revision, state.consecutive_regulation_turns, state.activity_sequence) == (5, 4, 5)
    assert state.pending_regulation_event.ordinal == 5
    events = SQLiteTutoringRepository(path).load_regulation_events("session")
    assert [event.ordinal for event in events] == [1, 2, 3, 4, 5]
    assert events[-2].outcome.value == "repeated_difficulty"
    assert events[-1].strategy is ExecutedRegulationAction.CAP_CHOICE


def test_distinct_commands_with_the_same_regulation_revision_conflict(tmp_path):
    _, repo = setup_repository(tmp_path)
    def batch(command_id):
        return MutationBatch(
            command_id, command_id, "session", 1, 1,
            CommandResult(command_id, CommandStatus.APPLIED, "applied"),
            expected_regulation_revision=0,
            regulation_mutation=RegulationMutation(
                1, 1, 1, ExecutedRegulationAction.SIMPLIFY_LANGUAGE
            ),
        )
    assert repo.commit_once(batch("first")).outcome is CommitOutcome.APPLIED
    second = repo.commit_once(batch("second"))
    assert (second.outcome, second.reason) == (CommitOutcome.CONFLICT, "stale-regulation-revision")


def test_ordered_hint_and_regulation_revision_roll_back_together(tmp_path):
    _, repo = setup_repository(tmp_path); runtime = SessionRuntime(); service = make_service(repo, runtime)
    with repo._connect() as db:
        db.execute("CREATE TRIGGER fail_regulation BEFORE UPDATE ON regulation_state BEGIN SELECT RAISE(ABORT, 'fail'); END")
    result = regulate(service, runtime, number=1, revision=0, strategy=PedagogicalStrategy.GIVE_ORDERED_HINT)
    assert result.status is CommandStatus.PERSISTENCE_FAILED
    state = repo.load_state("session")
    assert state.regulation_revision == 0
    assert state.progress_for("activity").hints_used == 0
    assert state.pending_regulation_event is None
    assert repo.load_regulation_events("session") == ()
    assert repo.load_command_result("regulate-1") is None
