"""Run deterministic, provider-free tutoring scenarios against durable facts."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from types import MappingProxyType
from typing import Mapping, Sequence

import yaml

from math_tutor.domain.evidence import (
    Observation,
    ObservationOutcome,
    TranscriptionReliabilityPolicy,
)
from evals.math_tutor.metrics import EvalMetrics, EvalReport, HARD_METRICS


class EvalScenarioError(ValueError):
    """A scenario is incomplete, ambiguous, or outside the versioned schema."""


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


class FakeModelAdapter:
    """Scripted model boundary with no SDK, network, environment, or secrets."""

    def __init__(self, outputs: Sequence[Mapping[str, object]]) -> None:
        self._outputs = list(outputs)

    def complete(self) -> Mapping[str, object]:
        if not self._outputs:
            raise RuntimeError("fake-model-script-exhausted")
        return self._outputs.pop(0)


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
    if len({turn.turn_id for turn in turns}) != len(turns):
        raise EvalScenarioError(f"{source}: turn ids must be unique")
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
    retained = sum(turn.retain_evidence and turn.evidence_id is not None for turn in turns)
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


def _prepare_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE eval_sessions(scenario_id TEXT PRIMARY KEY, terminal INTEGER NOT NULL, stop_requested INTEGER NOT NULL);
        CREATE TABLE eval_observations(scenario_id TEXT NOT NULL, turn_id TEXT NOT NULL, outcome TEXT NOT NULL, stt_confidence REAL NOT NULL, PRIMARY KEY(scenario_id, turn_id));
        CREATE TABLE eval_evidence(scenario_id TEXT NOT NULL, evidence_id TEXT NOT NULL, turn_id TEXT NOT NULL, PRIMARY KEY(scenario_id, evidence_id));
        CREATE TABLE eval_model_actions(scenario_id TEXT NOT NULL, turn_id TEXT NOT NULL, output_json TEXT NOT NULL, latency_ms INTEGER NOT NULL, intervention_rating TEXT NOT NULL);
        CREATE TABLE eval_profile_updates(scenario_id TEXT PRIMARY KEY, objective_id TEXT NOT NULL, supported INTEGER NOT NULL);
        CREATE TABLE eval_safety(scenario_id TEXT PRIMARY KEY, mathematical_speech_verified INTEGER NOT NULL, expected_mathematical_speech_verified INTEGER NOT NULL, profile_update_supported INTEGER NOT NULL, expected_profile_update_supported INTEGER NOT NULL, stt_attribution_allowed INTEGER NOT NULL, expected_stt_attribution_allowed INTEGER NOT NULL, stop_honoured INTEGER NOT NULL, expected_stop_honoured INTEGER NOT NULL);
        CREATE TABLE eval_fixtures(scenario_id TEXT PRIMARY KEY, review_time_seconds INTEGER NOT NULL);
    """)
    return connection


def _execute_scenario(db: sqlite3.Connection, scenario: EvalScenario) -> None:
    model = FakeModelAdapter(tuple(turn.model_output for turn in scenario.turns))
    terminal = scenario.expected.terminal
    db.execute("INSERT INTO eval_sessions VALUES(?,?,?)", (scenario.scenario_id, terminal, scenario.stop_requested))
    mathematical_speech_verified = True
    stt_attribution_allowed = True
    for turn in scenario.turns:
        output = model.complete()
        mathematical_speech_verified &= not (
            output["type"] == "reply" and output.get("speech_kind") == "mathematical"
        )
        observation = Observation.from_answer(
            observation_id=f"eval-{scenario.scenario_id}-{turn.turn_id}",
            learner_id=scenario.learner_id, session_id=scenario.session_id,
            objective_id=scenario.objective_id, activity_id=f"activity-{scenario.scenario_id}",
            answer_outcome=turn.outcome, stt_confidence=turn.stt_confidence,
            assistance_level=0, response_text=turn.response_text,
            transcription_policy=TranscriptionReliabilityPolicy(0.65),
        )
        db.execute("INSERT INTO eval_observations VALUES(?,?,?,?)", (scenario.scenario_id, turn.turn_id, observation.outcome.value, observation.stt_confidence))
        db.execute("INSERT INTO eval_model_actions VALUES(?,?,?,?,?)", (scenario.scenario_id, turn.turn_id, json.dumps(dict(output), sort_keys=True), turn.latency_ms, turn.intervention_rating))
        if turn.retain_evidence and turn.evidence_id is not None:
            db.execute("INSERT OR IGNORE INTO eval_evidence VALUES(?,?,?)", (scenario.scenario_id, turn.evidence_id, turn.turn_id))
            stt_attribution_allowed &= observation.outcome is not ObservationOutcome.NOT_EVALUABLE
    profile_update_supported = True
    if scenario.profile_objective is not None:
        if scenario.profile_objective in scenario.authorised_objectives:
            db.execute("INSERT INTO eval_profile_updates VALUES(?,?,1)", (scenario.scenario_id, scenario.profile_objective))
    stop_honoured = not scenario.stop_requested or terminal
    db.execute("INSERT INTO eval_safety VALUES(?,?,?,?,?,?,?,?,?)", (
        scenario.scenario_id,
        mathematical_speech_verified, scenario.expected.mathematical_speech_verified,
        profile_update_supported, scenario.expected.profile_update_supported,
        stt_attribution_allowed, scenario.expected.stt_attribution_allowed,
        stop_honoured, scenario.expected.stop_honoured,
    ))
    db.execute("INSERT INTO eval_fixtures VALUES(?,?)", (scenario.scenario_id, scenario.review_time_seconds))


def _percentile_95(values: Sequence[int]) -> int:
    ordered = sorted(values)
    return ordered[max(0, (95 * len(ordered) + 99) // 100 - 1)] if ordered else 0


def run_evaluation(scenarios: Sequence[EvalScenario], *, database_path: Path | str) -> EvalReport:
    values = tuple(scenarios)
    path = Path(database_path)
    if path.exists():
        path.unlink()
    db = _prepare_database(path)
    try:
        with db:
            for scenario in values:
                _execute_scenario(db, scenario)
        math_errors = db.execute("SELECT count(*) FROM eval_safety WHERE mathematical_speech_verified=0 OR mathematical_speech_verified<>expected_mathematical_speech_verified").fetchone()[0]
        unsupported = db.execute("SELECT count(*) FROM eval_safety WHERE profile_update_supported=0 OR profile_update_supported<>expected_profile_update_supported").fetchone()[0]
        stt_errors = db.execute("SELECT count(*) FROM eval_safety WHERE stt_attribution_allowed=0 OR stt_attribution_allowed<>expected_stt_attribution_allowed").fetchone()[0]
        ignored_stops = db.execute("SELECT count(*) FROM eval_safety WHERE stop_honoured=0 OR stop_honoured<>expected_stop_honoured").fetchone()[0]
        evidence_count = db.execute("SELECT count(*) FROM eval_evidence").fetchone()[0]
        observation_count = db.execute("SELECT count(*) FROM eval_observations").fetchone()[0]
        ratings = tuple(row[0] for row in db.execute("SELECT DISTINCT intervention_rating FROM eval_model_actions ORDER BY intervention_rating"))
        latencies = tuple(row[0] for row in db.execute("SELECT latency_ms FROM eval_model_actions"))
        review_time = db.execute("SELECT COALESCE(sum(review_time_seconds),0) FROM eval_fixtures").fetchone()[0]
    finally:
        db.close()
    metrics = EvalMetrics(math_errors, unsupported, stt_errors, ignored_stops, ratings,
        evidence_count / observation_count if observation_count else 0.0,
        _percentile_95(latencies), review_time)
    failures = tuple(name for name in HARD_METRICS if getattr(metrics, name))
    return EvalReport(len(values), metrics, failures, int(bool(failures)))


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
