from dataclasses import replace
from pathlib import Path

import pytest

from evals.math_tutor.runner import (
    EvalScenarioError,
    load_scenarios,
    run_evaluation,
)


SCENARIOS = Path("evals/math_tutor/scenarios")


def test_offline_eval_catalog_is_strict_and_covers_required_behaviours(tmp_path):
    scenarios = load_scenarios(SCENARIOS)

    assert {scenario.scenario_id for scenario in scenarios} == {
        "ambiguous-language",
        "conceptual-error",
        "correct-answer",
        "frustration",
        "hint-exhaustion",
        "low-stt-confidence",
        "out-of-scope-objective",
        "replayed-evidence",
        "self-correction",
        "stop-request",
    }

    source = (SCENARIOS / "correct-answer.yaml").read_text()
    (tmp_path / "unknown.yaml").write_text(source + "unknown: true\n")
    with pytest.raises(EvalScenarioError, match="unknown field"):
        load_scenarios(tmp_path)

    (tmp_path / "missing.yaml").write_text("schema_version: 1\n")
    with pytest.raises(EvalScenarioError, match="missing field"):
        load_scenarios(tmp_path)


def test_eval_gate_grades_durable_state_and_is_deterministic(tmp_path):
    scenarios = load_scenarios(SCENARIOS)

    first = run_evaluation(scenarios, database_path=tmp_path / "first.db")
    second = run_evaluation(scenarios, database_path=tmp_path / "second.db")

    assert first == second
    assert first.exit_code == 0
    assert first.scenarios_run == 10
    assert first.hard_failures == ()
    assert first.metrics.mathematical_speech_errors == 0
    assert first.metrics.unsupported_profile_updates == 0
    assert first.metrics.stt_misattributions == 0
    assert first.metrics.ignored_stops == 0
    assert first.metrics.evidence_coverage > 0
    assert first.metrics.latency_ms_p95 >= 0
    assert first.metrics.review_time_seconds >= 0
    assert set(first.metrics.intervention_ratings) == {
        "appropriate",
        "needs-review",
    }


@pytest.mark.parametrize(
    ("field", "expected_metric"),
    [
        ("mathematical_speech_verified", "mathematical_speech_errors"),
        ("profile_update_supported", "unsupported_profile_updates"),
        ("stt_attribution_allowed", "stt_misattributions"),
        ("stop_honoured", "ignored_stops"),
    ],
)
def test_each_hard_invariant_returns_nonzero_and_names_its_metric(
    tmp_path, field, expected_metric
):
    scenario = load_scenarios(SCENARIOS)[0]
    broken = replace(scenario, expected=replace(scenario.expected, **{field: False}))

    report = run_evaluation((broken,), database_path=tmp_path / f"{field}.db")

    assert report.exit_code != 0
    assert expected_metric in report.hard_failures
    assert getattr(report.metrics, expected_metric) == 1


def test_eval_runner_never_uses_network_or_provider_secrets(tmp_path, monkeypatch):
    for key in ("OPENAI_API_KEY", "LLM_API_KEY", "STT_API_KEY", "TTS_API_KEY"):
        monkeypatch.setenv(key, "must-not-be-read")

    report = run_evaluation(
        load_scenarios(SCENARIOS), database_path=tmp_path / "offline.db"
    )

    assert report.exit_code == 0
    assert report.execution_mode == "offline-fake-model"
