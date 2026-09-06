from pathlib import Path
import sqlite3

import pytest

from evals.math_tutor.runner import (
    FaultAdapter,
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
    assert first.durable_outcomes["low-stt-confidence"].not_evaluable == 1
    assert first.durable_outcomes["low-stt-confidence"].evidence_count == 0
    assert first.durable_outcomes["stop-request"].terminal
    assert first.durable_outcomes["replayed-evidence"].evidence_count == 1
    # The selected reviewed template exposes one hint; three later requests are rejected.
    assert first.durable_outcomes["hint-exhaustion"].hints_used == 1
    assert first.durable_outcomes["correct-answer"].correct == 1
    assert first.durable_outcomes["conceptual-error"].incorrect == 1
    assert first.durable_outcomes["self-correction"].correct == 1
    assert first.durable_outcomes["self-correction"].incorrect == 1
    assert first.durable_outcomes["ambiguous-language"].ambiguous == 1
    assert first.durable_outcomes["frustration"].observations == 0
    assert first.durable_outcomes["out-of-scope-objective"].profile_proposals == 0
    assert first.durable_outcomes["self-correction"].observation_sequence == (
        "incorrect", "correct"
    )
    assert first.durable_outcomes["hint-exhaustion"].repair_calls == 3
    assert first.durable_outcomes["frustration"].intervention == "supportive-social"


def test_any_versioned_expected_behaviour_mismatch_fails_the_cli_gate(tmp_path):
    source = (SCENARIOS / "correct-answer.yaml").read_text().replace(
        "correct: 1", "correct: 2"
    )
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    (scenario_dir / "regression.yaml").write_text(source)
    report = run_evaluation(
        load_scenarios(scenario_dir), database_path=tmp_path / "regression.db"
    )
    assert report.exit_code != 0
    assert "correct-answer.correct" in report.hard_failures


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
    report = run_evaluation(
        load_scenarios(SCENARIOS),
        database_path=tmp_path / f"{field}.db",
        faults=FaultAdapter(**{field: True}),
    )

    assert report.exit_code != 0
    assert expected_metric in report.hard_failures
    assert getattr(report.metrics, expected_metric) == 1
    outcome = report.durable_outcomes[
        {
            "mathematical_speech_verified": "correct-answer",
            "profile_update_supported": "correct-answer",
            "stt_attribution_allowed": "low-stt-confidence",
            "stop_honoured": "stop-request",
        }[field]
    ]
    if field == "mathematical_speech_verified":
        assert "2 + 2 = 5" in " ".join(outcome.released_speech)
    elif field == "profile_update_supported":
        assert outcome.profile_proposals == 1
    elif field == "stt_attribution_allowed":
        assert outcome.evidence_count == 1
    else:
        assert not outcome.terminal


def test_diagnostic_or_private_narrative_is_a_hard_failure(tmp_path):
    report = run_evaluation(
        load_scenarios(SCENARIOS),
        database_path=tmp_path / "privacy.db",
        faults=FaultAdapter(diagnostic_or_private_narrative=True),
    )
    assert report.exit_code != 0
    assert report.metrics.diagnostic_or_privacy_violations == 1
    assert "diagnostic_or_privacy_violations" in report.hard_failures
    assert any(
        "diagnóstico" in speech
        for outcome in report.durable_outcomes.values()
        for speech in outcome.model_artifacts
    )


def test_eval_runner_never_uses_network_or_provider_secrets(tmp_path, monkeypatch):
    for key in ("OPENAI_API_KEY", "LLM_API_KEY", "STT_API_KEY", "TTS_API_KEY"):
        monkeypatch.setenv(key, "must-not-be-read")

    report = run_evaluation(
        load_scenarios(SCENARIOS), database_path=tmp_path / "offline.db"
    )

    assert report.exit_code == 0
    assert report.execution_mode == "offline-fake-model"
