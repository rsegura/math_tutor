from dataclasses import replace

from math_tutor.application.ports import (
    ActivityProgress,
    CommitDecision,
    PersistedTutoringState,
    StoredCommandResult,
)
from math_tutor.application.regulation import ExecutedRegulationAction, RegulationResult
from math_tutor.application.results import CommandStatus
from math_tutor.application.service import CommitRegulation, TutoringService
from math_tutor.application.session_runtime import SessionRuntime
from math_tutor.domain.activities import Activity, StructuredAnswer
from math_tutor.domain.evidence import TranscriptionReliabilityPolicy
from math_tutor.domain.learning import (
    AssistanceThreshold,
    CompetencyState,
    LearningPlan,
    LearningSession,
    PresentationProfile,
    ProgressionPolicy,
)
from math_tutor.domain.regulation import (
    ConfidenceBand,
    ConversationalSignal,
    PedagogicalStrategy,
    RegulationPolicy,
)
from math_tutor.domain.templates import ExpectedAnswerKind
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs
from pathlib import Path


ROOT = Path(__file__).parents[4]


class Repository:
    def __init__(self):
        plan = LearningPlan("learner", ("units-tens",), ("units-tens",), PresentationProfile.for_age(8))
        self.state = PersistedTutoringState(
            LearningSession.start(session_id="session", plan=plan),
            1,
            (ActivityProgress("activity", 0, 0, 1),),
            regulation_revision=2,
            consecutive_regulation_turns=1,
        )
        self.activity = Activity(
            "template", "units-tens", 1, "¿Cuántas unidades hay?", {"number": 12},
            StructuredAnswer.evaluable(ExpectedAnswerKind.INTEGER, {"answer": 2}),
            (), ("hint-1",),
        )
        self.results = {}
        self.batches = []

    def load_command_result(self, command_id): return self.results.get(command_id)
    def load_state(self, session_id): return self.state
    def load_activity(self, session_id, activity_id): return self.activity if activity_id == "activity" else None
    def commit_once(self, batch):
        self.batches.append(batch)
        if batch.expected_regulation_revision != self.state.regulation_revision:
            return CommitDecision.conflict("stale-regulation-revision")
        self.results[batch.command_id] = StoredCommandResult(batch.command_fingerprint, batch.result)
        return CommitDecision.applied(batch.result, batch.command_fingerprint)


def make_service(repository, runtime):
    thresholds = tuple(AssistanceThreshold(state, 3) for state in (
        CompetencyState.EXPLORING,
        CompetencyState.WITH_INTENSIVE_HELP,
        CompetencyState.WITH_LIGHT_HELP,
        CompetencyState.INDEPENDENT,
        CompetencyState.GENERALIZED,
    ))
    catalog = load_curriculum_catalogs(
        ROOT / "src/math_tutor/curricula/primary-math-v1.yaml",
        ROOT / "src/math_tutor/curricula/activity-templates-v1.yaml",
    )[1]
    return TutoringService(
        repository, runtime, catalog,
        TranscriptionReliabilityPolicy(.75), ProgressionPolicy(thresholds),
        reviewed_hint_texts={"hint-1": "Cuenta solo las unidades."},
    )


def command(strategy=PedagogicalStrategy.SIMPLIFY_LANGUAGE, **changes):
    values = dict(
        command_id="cmd", session_id="session", expected_session_version=1,
        expected_profile_version=1, generation_id="gen-1", turn_id="turn-1",
        activity_id="activity", expected_regulation_revision=2,
        signal=ConversationalSignal.CONFUSED, confidence_band=ConfidenceBand.MEDIUM,
        strategy=strategy, regulation_policy=RegulationPolicy(tuple(PedagogicalStrategy), 4),
        presentation=("clear-and-encouraging",), adaptations=(),
    )
    values.update(changes)
    return CommitRegulation(**values)


def test_regulation_commits_canonical_speech_and_revision_without_competence_mutation():
    repository = Repository(); runtime = SessionRuntime(); runtime.start_generation("session")

    result = make_service(repository, runtime).commit_regulation(command())

    assert result.status is CommandStatus.APPLIED
    assert result.payload == RegulationResult(
        speech="Vamos paso a paso. ¿Cuántas unidades hay?",
        executed_action=ExecutedRegulationAction.SIMPLIFY_LANGUAGE,
        regulation_revision=3,
    )
    batch = repository.batches[0]
    assert batch.regulation_mutation.next_revision == 3
    assert batch.regulation_mutation.consecutive_turns == 2
    assert batch.observations == batch.evidence == batch.profile_change_proposals == ()


def test_ordered_hint_is_part_of_the_same_regulation_mutation_batch():
    repository = Repository(); runtime = SessionRuntime(); runtime.start_generation("session")

    result = make_service(repository, runtime).commit_regulation(
        command(PedagogicalStrategy.GIVE_ORDERED_HINT)
    )

    assert result.payload.speech == "Cuenta solo las unidades. ¿Cuántas unidades hay?"
    assert len(repository.batches) == 1
    assert repository.batches[0].activity_progress[0].hints_used == 1
    assert repository.batches[0].regulation_mutation.next_revision == 3


def test_exhausted_ordered_hint_falls_back_to_canonical_simplification():
    repository = Repository()
    repository.state = replace(
        repository.state,
        activity_progress=(replace(repository.state.activity_progress[0], hints_used=1),),
    )
    runtime = SessionRuntime(); runtime.start_generation("session")

    result = make_service(repository, runtime).commit_regulation(
        command(PedagogicalStrategy.GIVE_ORDERED_HINT)
    )

    assert result.payload.executed_action is ExecutedRegulationAction.SIMPLIFY_LANGUAGE
    assert result.payload.speech == "Vamos paso a paso. ¿Cuántas unidades hay?"
    assert repository.batches[0].activity_progress == ()


def test_exhausted_hint_uses_authorised_repeat_when_simplify_is_disabled():
    repository = Repository()
    repository.state = replace(
        repository.state,
        activity_progress=(replace(repository.state.activity_progress[0], hints_used=1),),
    )
    runtime = SessionRuntime(); runtime.start_generation("session")
    policy = RegulationPolicy((
        PedagogicalStrategy.GIVE_ORDERED_HINT,
        PedagogicalStrategy.REPEAT_INSTRUCTION,
        PedagogicalStrategy.VALIDATE_EMOTION,
        PedagogicalStrategy.REDIRECT_GENTLY,
        PedagogicalStrategy.TAKE_SHORT_PAUSE,
    ), 4)

    result = make_service(repository, runtime).commit_regulation(command(
        PedagogicalStrategy.GIVE_ORDERED_HINT, regulation_policy=policy,
    ))

    assert result.payload.executed_action is ExecutedRegulationAction.REPEAT_INSTRUCTION
    assert result.payload.speech == "¿Cuántas unidades hay?"
    assert repository.batches[0].regulation_mutation.pending_event.strategy is ExecutedRegulationAction.REPEAT_INSTRUCTION


def test_missing_reviewed_hint_content_uses_authorised_durable_fallback():
    repository = Repository(); runtime = SessionRuntime(); runtime.start_generation("session")
    tutor = make_service(repository, runtime)
    tutor._reviewed_hint_texts = {}

    result = tutor.commit_regulation(command(PedagogicalStrategy.GIVE_ORDERED_HINT))

    assert result.status is CommandStatus.APPLIED
    assert result.payload.executed_action is ExecutedRegulationAction.SIMPLIFY_LANGUAGE
    assert repository.batches[0].activity_progress == ()


def test_short_instruction_profile_selects_a_reviewed_concise_variant():
    repository = Repository(); runtime = SessionRuntime(); runtime.start_generation("session")

    result = make_service(repository, runtime).commit_regulation(command(
        presentation=("short-instructions",),
    ))

    assert result.payload.speech == "Vamos despacio. ¿Cuántas unidades hay?"


def test_extra_repetition_adaptation_selects_a_reviewed_repeat_variant():
    repository = Repository(); runtime = SessionRuntime(); runtime.start_generation("session")

    result = make_service(repository, runtime).commit_regulation(command(
        PedagogicalStrategy.REPEAT_INSTRUCTION,
        adaptations=("extra-repetition",),
    ))

    assert result.payload.speech == "Te lo repito. ¿Cuántas unidades hay?"


def test_cap_uses_neutral_choice_without_advancing_counter():
    repository = Repository()
    repository.state = replace(repository.state, consecutive_regulation_turns=4)
    runtime = SessionRuntime(); runtime.start_generation("session")

    result = make_service(repository, runtime).commit_regulation(command())

    assert result.payload.speech == "¿Quieres continuar o hacer una pausa?"
    assert result.payload.executed_action is ExecutedRegulationAction.CAP_CHOICE
    assert repository.batches[0].regulation_mutation.consecutive_turns == 4
    assert repository.batches[0].regulation_mutation.activity_sequence == 1
    assert repository.batches[0].regulation_mutation.executed_action is ExecutedRegulationAction.CAP_CHOICE


def test_stale_persisted_regulation_revision_rejects_before_speech_result():
    repository = Repository(); runtime = SessionRuntime(); runtime.start_generation("session")

    result = make_service(repository, runtime).commit_regulation(
        command(expected_regulation_revision=1)
    )

    assert result.status is CommandStatus.REJECTED
    assert result.reason == "stale-regulation-revision"
    assert repository.batches == []


def test_stale_generation_is_rejected_before_repository_mutation():
    repository = Repository(); runtime = SessionRuntime(); runtime.start_generation("session")

    result = make_service(repository, runtime).commit_regulation(
        command(generation_id="gen-old")
    )

    assert result.status is CommandStatus.REJECTED
    assert result.reason == "generation-not-active"
    assert repository.batches == []
