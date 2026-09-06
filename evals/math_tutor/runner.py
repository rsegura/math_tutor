"""Run deterministic, provider-free tutoring scenarios against durable facts."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Event
from types import MappingProxyType
from typing import Mapping, Sequence

import yaml

from math_tutor.domain.evidence import ObservationOutcome
from math_tutor.agent.runtime_factory import (
    BoundedConversationEngine,
    ProviderSettings,
    TutoringVoiceRuntime,
)
from math_tutor.agent.voice_agent import VoiceDecision, VoiceTurn
from math_tutor.application.provisioning import (
    CreateLearner,
    CreateLearningPlan,
    ProvisioningService,
    SessionLimits,
    StartLearningSession,
)
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs
from math_tutor.infrastructure.dispatch import DispatchMetadata
from math_tutor.infrastructure.persistence.migrator import migrate
from math_tutor.infrastructure.persistence.repositories import SQLiteTutoringRepository
from evals.math_tutor.metrics import DurableOutcome, EvalMetrics, EvalReport, HARD_METRICS


class EvalScenarioError(ValueError):
    """A scenario is incomplete, ambiguous, or outside the versioned schema."""


@dataclass(frozen=True, slots=True)
class FaultInjection:
    """Test-only faulty boundary used to prove every hard gate trips."""

    mathematical_speech_verified: bool = False
    profile_update_supported: bool = False
    stt_attribution_allowed: bool = False
    stop_honoured: bool = False
    diagnostic_or_private_narrative: bool = False


@dataclass(frozen=True, slots=True)
class ScenarioExpected:
    mathematical_speech_verified: bool
    profile_update_supported: bool
    stt_attribution_allowed: bool
    stop_honoured: bool
    evidence_count: int
    terminal: bool


@dataclass(frozen=True, slots=True)
class EvalTurn:
    turn_id: str
    response_text: str
    stt_confidence: float
    outcome: str
    retain_evidence: bool
    evidence_id: str | None
    model_output: Mapping[str, object]
    intervention_rating: str
    latency_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_output", MappingProxyType(dict(self.model_output)))


@dataclass(frozen=True, slots=True)
class EvalScenario:
    schema_version: int
    scenario_id: str
    description: str
    learner_id: str
    session_id: str
    objective_id: str
    stop_requested: bool
    profile_objective: str | None
    authorised_objectives: tuple[str, ...]
    turns: tuple[EvalTurn, ...]
    expected: ScenarioExpected
    review_time_seconds: int


_ROOT_FIELDS = {
    "schema_version", "scenario_id", "description", "learner_id", "session_id",
    "objective_id", "stop_requested", "profile_objective", "authorised_objectives",
    "turns", "expected", "review_time_seconds",
}
_TURN_FIELDS = {
    "turn_id", "response_text", "stt_confidence", "outcome", "retain_evidence",
    "evidence_id", "model_output", "intervention_rating", "latency_ms",
}
_EXPECTED_FIELDS = {
    "mathematical_speech_verified", "profile_update_supported",
    "stt_attribution_allowed", "stop_honoured", "evidence_count", "terminal",
}


def _exact_mapping(value: object, fields: set[str], where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise EvalScenarioError(f"{where}: expected object")
    if not all(isinstance(key, str) for key in value):
        raise EvalScenarioError(f"{where}: field names must be strings")
    unknown = sorted(set(value) - fields)
    missing = sorted(fields - set(value))
    if unknown:
        raise EvalScenarioError(f"{where}: unknown field '{unknown[0]}'")
    if missing:
        raise EvalScenarioError(f"{where}: missing field '{missing[0]}'")
    return value


def _boolean(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise EvalScenarioError(f"{where}: expected boolean")
    return value


def _integer(value: object, where: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise EvalScenarioError(f"{where}: expected integer >= {minimum}")
    return value


def _text(value: object, where: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise EvalScenarioError(f"{where}: expected nonempty text")
    return value


def _parse_scenario(raw: object, source: Path) -> EvalScenario:
    data = _exact_mapping(raw, _ROOT_FIELDS, str(source))
    if data["schema_version"] != 1:
        raise EvalScenarioError(f"{source}: unsupported schema version")
    objective_values = data["authorised_objectives"]
    if not isinstance(objective_values, list) or not objective_values:
        raise EvalScenarioError(f"{source}: authorised_objectives must be a nonempty list")
    authorised = tuple(_text(item, f"{source}: authorised objective") for item in objective_values)
    if len(set(authorised)) != len(authorised):
        raise EvalScenarioError(f"{source}: authorised objectives must be unique")
    raw_turns = data["turns"]
    if not isinstance(raw_turns, list) or not raw_turns:
        raise EvalScenarioError(f"{source}: turns must be a nonempty list")
    turns: list[EvalTurn] = []
    for index, value in enumerate(raw_turns):
        turn = _exact_mapping(value, _TURN_FIELDS, f"{source}: turn {index}")
        confidence = turn["stt_confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise EvalScenarioError(f"{source}: turn {index}: invalid STT confidence")
        outcome = _text(turn["outcome"], f"{source}: turn {index}: outcome")
        try:
            ObservationOutcome(outcome)
        except ValueError:
            raise EvalScenarioError(f"{source}: turn {index}: unknown outcome") from None
        model_output = turn["model_output"]
        if not isinstance(model_output, dict):
            raise EvalScenarioError(f"{source}: turn {index}: model_output must be an object")
        output_type = model_output.get("type")
        output_fields = {"type", "name"} if output_type == "tool" else {
            "type", "speech", "speech_kind"
        } if output_type == "reply" else set()
        if not output_fields:
            raise EvalScenarioError(f"{source}: turn {index}: unknown model output type")
        _exact_mapping(model_output, output_fields, f"{source}: turn {index}: model_output")
        evidence_id = _text(turn["evidence_id"], f"{source}: turn {index}: evidence_id", nullable=True)
        turns.append(EvalTurn(
            turn_id=_text(turn["turn_id"], f"{source}: turn {index}: turn_id"),
            response_text=_text(turn["response_text"], f"{source}: turn {index}: response_text"),
            stt_confidence=float(confidence), outcome=outcome,
            retain_evidence=_boolean(turn["retain_evidence"], f"{source}: turn {index}: retain_evidence"),
            evidence_id=evidence_id, model_output=model_output,
            intervention_rating=_text(turn["intervention_rating"], f"{source}: turn {index}: intervention_rating"),
            latency_ms=_integer(turn["latency_ms"], f"{source}: turn {index}: latency_ms"),
        ))
    expected_raw = _exact_mapping(data["expected"], _EXPECTED_FIELDS, f"{source}: expected")
    expected = ScenarioExpected(
        mathematical_speech_verified=_boolean(expected_raw["mathematical_speech_verified"], f"{source}: mathematical_speech_verified"),
        profile_update_supported=_boolean(expected_raw["profile_update_supported"], f"{source}: profile_update_supported"),
        stt_attribution_allowed=_boolean(expected_raw["stt_attribution_allowed"], f"{source}: stt_attribution_allowed"),
        stop_honoured=_boolean(expected_raw["stop_honoured"], f"{source}: stop_honoured"),
        evidence_count=_integer(expected_raw["evidence_count"], f"{source}: evidence_count"),
        terminal=_boolean(expected_raw["terminal"], f"{source}: terminal"),
    )
    scenario = EvalScenario(
        schema_version=1,
        scenario_id=_text(data["scenario_id"], f"{source}: scenario_id"),
        description=_text(data["description"], f"{source}: description"),
        learner_id=_text(data["learner_id"], f"{source}: learner_id"),
        session_id=_text(data["session_id"], f"{source}: session_id"),
        objective_id=_text(data["objective_id"], f"{source}: objective_id"),
        stop_requested=_boolean(data["stop_requested"], f"{source}: stop_requested"),
        profile_objective=_text(data["profile_objective"], f"{source}: profile_objective", nullable=True),
        authorised_objectives=authorised, turns=tuple(turns), expected=expected,
        review_time_seconds=_integer(data["review_time_seconds"], f"{source}: review_time_seconds"),
    )
    retained = len({turn.evidence_id for turn in turns if turn.retain_evidence and turn.evidence_id is not None})
    if retained != expected.evidence_count:
        raise EvalScenarioError(f"{source}: expected evidence count disagrees with turns")
    return scenario


def load_scenarios(directory: Path | str) -> tuple[EvalScenario, ...]:
    path = Path(directory)
    files = sorted(path.glob("*.yaml"))
    if not files:
        raise EvalScenarioError(f"{path}: no scenarios")
    scenarios = tuple(_parse_scenario(yaml.safe_load(file.read_text()), file) for file in files)
    ids = tuple(item.scenario_id for item in scenarios)
    if len(set(ids)) != len(ids):
        raise EvalScenarioError(f"{path}: scenario ids must be unique")
    return tuple(sorted(scenarios, key=lambda item: item.scenario_id))


class _NoopPurger:
    def purge_consent_scope(self, consent_id: str, session_ids: tuple[str, ...]) -> None:
        return None


class ProductionFakeModelAdapter:
    """Turns scenario intent into valid proposals at the real model port."""

    def __init__(self, scenario: EvalScenario) -> None:
        self._scenario = scenario
        self._turns = {
            f"{scenario.scenario_id}-{turn.turn_id}": turn for turn in scenario.turns
        }
        self.seen_contexts: list[object] = []
        self.outputs: list[Mapping[str, object]] = []

    def complete(self, *, context, repair: bool, **_: object) -> Mapping[str, object]:
        self.seen_contexts.append(context)
        turn = self._turns[context.current_turn.turn_id]
        if repair:
            output = {"type": "reply", "speech": "Vamos paso a paso.", "speech_kind": "social"}
        else:
            action = turn.model_output
            if action["type"] == "reply":
                output = dict(action)
            elif action["name"] == "record_answer":
                if turn.outcome == ObservationOutcome.AMBIGUOUS.value:
                    answer = {"status": "ambiguous", "kind": None, "values": {}}
                elif turn.outcome == ObservationOutcome.NOT_EVALUABLE.value:
                    answer = {"status": "not-evaluable", "kind": None, "values": {}}
                else:
                    values = dict(context.activity.expected_answer_fields and
                                  _active_activity_values(context, self._repository))
                    if turn.outcome == ObservationOutcome.INCORRECT.value:
                        first = next(iter(values))
                        value = values[first]
                        values[first] = value + 1 if isinstance(value, int) else "equal"
                    answer = {"status": "evaluable", "kind": context.activity.expected_answer_kind.value, "values": values}
                output = {"type": "tool", "name": "record_answer", "arguments": {"turn_id": context.current_turn.turn_id, "answer": answer}}
            elif action["name"] == "give_hint":
                output = {"type": "tool", "name": "give_hint", "arguments": {}}
            elif action["name"] == "propose_skill_update":
                output = {"type": "tool", "name": "propose_skill_update", "arguments": {"objective_id": self._scenario.profile_objective}}
            else:
                output = {"type": "tool", "name": action["name"], "arguments": {}}
        self.outputs.append(output)
        return output

    def bind_repository(self, repository: SQLiteTutoringRepository) -> None:
        self._repository = repository


def _active_activity_values(context, repository: SQLiteTutoringRepository) -> Mapping[str, object]:
    activity = repository.load_activity(context.session_id, context.activity.activity_id)
    if activity is None:
        raise RuntimeError("eval activity disappeared")
    return activity.expected_answer.values


def _providers() -> ProviderSettings:
    return ProviderSettings("deepgram", "offline", "unused", "openai", "offline", "unused", "openai", "offline", "offline", "unused", 4, 10)


def _bootstrap(repo: SQLiteTutoringRepository, scenario: EvalScenario, now: datetime):
    curriculum, _ = load_curriculum_catalogs(
        Path("src/math_tutor/curricula/primary-math-v1.yaml"),
        Path("src/math_tutor/curricula/activity-templates-v1.yaml"),
    )
    service = ProvisioningService(repo, curriculum, _NoopPurger())
    learner_id = f"{scenario.learner_id}-{scenario.scenario_id}"
    service.create_learner(CreateLearner(learner_id, "Alumno", 8))
    plan = service.create_learning_plan(CreateLearningPlan(
        f"plan-{scenario.scenario_id}", learner_id,
        scenario.authorised_objectives, ("short-instructions",), SessionLimits(10, 12),
    ))
    started = service.start_learning_session(StartLearningSession(learner_id), now=now)
    metadata = DispatchMetadata(started.tutoring_session_id, 1, plan.plan_id, plan.version)
    bootstrap = repo.reconstruct_voice_runtime(metadata)
    return TutoringVoiceRuntime(bootstrap, _providers())


@dataclass(frozen=True, slots=True)
class _Executed:
    scenario: EvalScenario
    outcome: DurableOutcome
    decisions: tuple[VoiceDecision, ...]
    model: ProductionFakeModelAdapter
    aggregate: object


async def _execute_scenario(repo: SQLiteTutoringRepository, scenario: EvalScenario) -> _Executed:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    runtime = _bootstrap(repo, scenario, now)
    model = ProductionFakeModelAdapter(scenario)
    model.bind_repository(repo)
    engine = BoundedConversationEngine(
        repository=repo, runtime=runtime,
        curricula_dir=Path("src/math_tutor/curricula"), now=lambda: now, model=model,
    )
    decisions: list[VoiceDecision] = []
    for turn in scenario.turns:
        turn_id = f"{scenario.scenario_id}-{turn.turn_id}"
        decisions.append(await engine.decide(VoiceTurn(turn_id, turn.response_text, turn.stt_confidence, Event())))
    aggregate = repo.load_session_aggregate(runtime.bootstrap.session.session_id)
    counts = {outcome: 0 for outcome in ObservationOutcome}
    for stored in aggregate.observations:
        counts[stored.observation.outcome] += 1
    outcome = DurableOutcome(
        len(aggregate.observations), counts[ObservationOutcome.CORRECT],
        counts[ObservationOutcome.INCORRECT], counts[ObservationOutcome.AMBIGUOUS],
        counts[ObservationOutcome.NOT_EVALUABLE], len(aggregate.evidence),
        len(aggregate.proposals),
        sum(item.attempts_used for item in aggregate.activity_progress),
        sum(item.hints_used for item in aggregate.activity_progress),
        aggregate.session.ended,
    )
    return _Executed(scenario, outcome, tuple(decisions), model, aggregate)


def _percentile_95(values: Sequence[int]) -> int:
    ordered = sorted(values)
    return ordered[max(0, (95 * len(ordered) + 99) // 100 - 1)] if ordered else 0


_FORBIDDEN = ("diagnóstico", "diagnostico", "lesión", "lesion", "neurológico", "neurologico", "historial médico", "telefono", "dirección")


def _unsafe_narrative(executed: _Executed) -> bool:
    texts = [decision.speech for decision in executed.decisions]
    texts.extend(turn.response_text for turn in executed.scenario.turns)
    texts.extend(str(output) for output in executed.model.outputs)
    texts.extend(str(context) for context in executed.model.seen_contexts)
    texts.extend(event.detail or "" for event in executed.aggregate.events)
    texts.extend(activity.prompt_es for activity in executed.aggregate.activities)
    texts.extend(
        revision.interpretation
        for evidence in executed.aggregate.evidence
        for revision in evidence.interpretations
    )
    texts.extend(str(proposal) for proposal in executed.aggregate.proposals)
    joined = " ".join(texts).casefold()
    return any(term in joined for term in _FORBIDDEN)


def _mathematical_speech_error(executed: _Executed) -> bool:
    import re
    for decision in executed.decisions:
        for left, operator, right, stated in re.findall(r"(-?\d+)\s*([+\-])\s*(-?\d+)\s*=\s*(-?\d+)", decision.speech):
            actual = int(left) + int(right) if operator == "+" else int(left) - int(right)
            if actual != int(stated):
                return True
    canonical = {"Vamos paso a paso.", "De acuerdo, paramos aquí.", "La sesión ha terminado por hoy.",
                 "No puedo continuar ahora. Terminamos por hoy."}
    prompts = {activity.prompt_es for activity in executed.aggregate.activities}
    for decision in executed.decisions:
        speech = decision.speech
        if speech in canonical or speech in prompts or speech.startswith(("Sí, esa respuesta es correcta.", "Esa respuesta todavía no es correcta.", "No estoy seguro", "No he podido comprobar")):
            continue
        reviewed_hints = {
            text for context in executed.model.seen_contexts
            for text in context.activity.hint_texts
        }
        if speech in reviewed_hints:
            continue
        return True
    return False


def run_evaluation(
    scenarios: Sequence[EvalScenario], *, database_path: Path | str,
    faults: FaultInjection | None = None,
) -> EvalReport:
    values = tuple(scenarios)
    path = Path(database_path)
    if path.exists():
        path.unlink()
    migrate(path)
    repo = SQLiteTutoringRepository(path)
    executed = tuple(asyncio.run(_execute_scenario(repo, scenario)) for scenario in values)
    fault = faults or FaultInjection()
    math_errors = sum(
        (actual := not _mathematical_speech_error(item))
        != item.scenario.expected.mathematical_speech_verified or not actual
        for item in executed
    ) + int(fault.mathematical_speech_verified)
    unsupported = sum(
        (actual := not any(
            proposal.objective_id not in item.aggregate.session.authorised_objective_ids
            or not proposal.evidence_ids for proposal in item.aggregate.proposals
        )) != item.scenario.expected.profile_update_supported or not actual
        for item in executed
    ) + int(fault.profile_update_supported)
    stt_errors = sum(
        (actual := not any(
            evidence.observation.outcome is ObservationOutcome.NOT_EVALUABLE
            for evidence in item.aggregate.evidence
        )) != item.scenario.expected.stt_attribution_allowed or not actual
        for item in executed
    ) + int(fault.stt_attribution_allowed)
    ignored_stops = sum(
        (actual := not item.scenario.stop_requested or item.aggregate.session.ended)
        != item.scenario.expected.stop_honoured or not actual
        for item in executed
    ) + int(fault.stop_honoured)
    privacy = sum(_unsafe_narrative(item) for item in executed) + int(fault.diagnostic_or_private_narrative)
    evidence_count = sum(item.outcome.evidence_count for item in executed)
    observation_count = sum(item.outcome.observations for item in executed)
    # Expected values are assertions only; they never populate observed facts.
    evidence_mismatches = sum(
        item.outcome.evidence_count != item.scenario.expected.evidence_count
        or item.outcome.terminal != item.scenario.expected.terminal
        for item in executed
    )
    ratings = tuple(sorted({turn.intervention_rating for scenario in values for turn in scenario.turns}))
    latencies = tuple(turn.latency_ms for scenario in values for turn in scenario.turns)
    review_time = sum(item.review_time_seconds for item in values)
    metrics = EvalMetrics(math_errors, unsupported, stt_errors, ignored_stops, privacy, ratings,
        evidence_count / observation_count if observation_count else 0.0, _percentile_95(latencies), review_time)
    failures = tuple(name for name in HARD_METRICS if getattr(metrics, name))
    if evidence_mismatches:
        failures = (*failures, "evidence_count_mismatch")
    outcomes = {item.scenario.scenario_id: item.outcome for item in executed}
    return EvalReport(len(values), metrics, failures, int(bool(failures)), outcomes)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=Path, default=Path("evals/math_tutor/scenarios"))
    parser.add_argument("--database", type=Path, default=Path("/tmp/math-tutor-evals.db"))
    args = parser.parse_args(argv)
    report = run_evaluation(load_scenarios(args.scenarios), database_path=args.database)
    print(json.dumps({
        "execution_mode": report.execution_mode,
        "scenarios_run": report.scenarios_run,
        "hard_failures": report.hard_failures,
        "metrics": {name: getattr(report.metrics, name) for name in EvalMetrics.__dataclass_fields__},
    }, sort_keys=True))
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
