import asyncio
from pathlib import Path
import inspect
from dataclasses import replace

import pytest
import math_tutor.application.regulation as production_regulation

from evals.math_tutor.runner import (
    FaultAdapter,
    EvalScenarioError,
    ProductionFakeModelAdapter,
    _providers,
    load_scenarios,
    run_evaluation,
)


SCENARIOS = Path("evals/math_tutor/scenarios")


def test_eval_provider_settings_use_named_fields_without_positional_drift():
    settings = _providers()

    assert settings.stt_provider == "deepgram"
    assert settings.llm_provider == "openai"
    assert settings.llm_base_url is None
    assert settings.tts_provider == "openai"
    assert settings.tts_voice_id == "offline"
    assert settings.llm_first_response_seconds == 4
    assert settings.llm_total_seconds == 10


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
        "regulation-confusion-paraphrase",
        "regulation-repetition",
        "regulation-frustration",
        "regulation-refusal",
        "regulation-off-task",
        "regulation-pause",
        "regulation-false-positive-answer",
        "regulation-disallowed-strategy",
        "regulation-cap",
        "regulation-replay-privacy",
        "regulation-stale-concurrency",
        "regulation-crash-reopen",
    }

    source = (SCENARIOS / "correct-answer.yaml").read_text()
    (tmp_path / "unknown.yaml").write_text(source + "unknown: true\n")
    with pytest.raises(EvalScenarioError, match="unknown field"):
        load_scenarios(tmp_path)
    (tmp_path / "unknown.yaml").unlink()
    (tmp_path / "missing.yaml").write_text("schema_version: 1\n")
    with pytest.raises(EvalScenarioError, match="missing field"):
        load_scenarios(tmp_path)


def test_fake_model_replays_explicit_fixture_without_oracle_or_repository_access():
    source = inspect.getsource(ProductionFakeModelAdapter)
    assert "expected" not in source
    assert "_repository" not in source


def test_unrelated_transcript_does_not_turn_explicit_wrong_arguments_into_correct_answer(tmp_path):
    scenario = next(item for item in load_scenarios(SCENARIOS) if item.scenario_id == "correct-answer")
    turn = replace(scenario.turns[0], response_text="esto no contiene la respuesta")
    wrong_output = dict(turn.model_output)
    wrong_output["arguments"] = dict(wrong_output["arguments"])
    wrong_output["arguments"]["answer"] = {
        "status": "evaluable", "kind": "integer", "values": {"answer": 999}
    }
    turn = replace(turn, model_output=wrong_output)
    report = run_evaluation((replace(scenario, turns=(turn,)),), database_path=tmp_path / "wrong.db")
    assert report.durable_outcomes["correct-answer"].incorrect == 1
    assert "correct-answer.correct" in report.hard_failures


def test_existing_unrelated_database_is_never_overwritten(tmp_path):
    path = tmp_path / "customer.db"
    path.write_bytes(b"keep-me")
    with pytest.raises(EvalScenarioError, match="already exists"):
        run_evaluation(load_scenarios(SCENARIOS), database_path=path)
    assert path.read_bytes() == b"keep-me"

    safe_dir = tmp_path / "math-tutor-eval-reusable"
    safe_dir.mkdir()
    safe = safe_dir / "eval-artifact.sqlite3"
    safe.write_bytes(b"old-eval")
    scenario = next(item for item in load_scenarios(SCENARIOS) if item.scenario_id == "frustration")
    report = run_evaluation(
        (scenario,), database_path=safe, overwrite_eval_db=True
    )
    assert report.exit_code == 0
    assert safe.read_bytes() != b"old-eval"
    with pytest.raises(EvalScenarioError, match="non-eval"):
        run_evaluation(
            load_scenarios(SCENARIOS), database_path=path, overwrite_eval_db=True
        )
    assert path.read_bytes() == b"keep-me"


def test_sensitive_child_ingress_is_not_itself_a_privacy_violation(tmp_path):
    scenario = next(item for item in load_scenarios(SCENARIOS) if item.scenario_id == "frustration")
    turn = replace(scenario.turns[0], response_text="mi correo es menor@example.com")
    report = run_evaluation((replace(scenario, turns=(turn,)),), database_path=tmp_path / "ingress.db")
    assert report.metrics.diagnostic_or_privacy_violations == 0
    outcome = report.durable_outcomes["frustration"]
    assert outcome.observations == 0
    assert "menor@example.com" not in " ".join(outcome.released_speech + outcome.model_artifacts)

def test_eval_gate_grades_durable_state_and_is_deterministic(tmp_path):
    scenarios = load_scenarios(SCENARIOS)

    first = run_evaluation(scenarios, database_path=tmp_path / "first.db")
    second = run_evaluation(scenarios, database_path=tmp_path / "second.db")

    assert first == second
    assert first.exit_code == 0
    assert first.scenarios_run == 22
    assert first.hard_failures == ()
    assert first.metrics.mathematical_speech_errors == 0
    assert first.metrics.unsupported_profile_updates == 0
    assert first.metrics.stt_misattributions == 0
    assert first.metrics.ignored_stops == 0
    assert first.metrics.evidence_coverage > 0
    assert first.metrics.latency_ms_p95 >= 0
    assert first.metrics.review_fixture_duration_seconds >= 0
    assert set(first.metrics.intervention_classifications) == {
        "ambiguous", "correct", "hint-cap-enforced", "incorrect",
            "no-observation", "replay-deduplicated", "scope-rejected",
        "self-correction-recorded", "stop-honoured", "supportive-social",
        "conversation-regulated", "regulation-replay-deduplicated",
    }
    assert set(first.metrics.intervention_rating_fixtures) == {"adequate", "correctable"}
    assert first.metrics.adequate_or_correctable_proportion == 1.0
    assert first.metrics.intervention_adequacy_target == 0.8
    assert first.durable_outcomes["low-stt-confidence"].observations == 0
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
    assert first.durable_outcomes["regulation-frustration"].regulation_signals == ("frustrated",)
    assert first.durable_outcomes["regulation-frustration"].structured_log_events == 2
    assert first.durable_outcomes["regulation-replay-privacy"].regulation_events == 1
    replay = first.durable_outcomes["regulation-replay-privacy"]
    assert "privado@example.com" not in " ".join(
        replay.released_speech + replay.model_artifacts
    )
    assert first.durable_outcomes["regulation-false-positive-answer"].observations == 1
    assert first.durable_outcomes["regulation-false-positive-answer"].regulation_events == 0
    assert first.durable_outcomes["regulation-disallowed-strategy"].repair_calls == 1
    assert first.durable_outcomes["regulation-cap"].regulation_strategies[-1] == "cap-choice"
    stale = first.durable_outcomes["regulation-stale-concurrency"]
    assert stale.regulation_events == 0
    assert not stale.stale_speech_released
    reopened = first.durable_outcomes["regulation-crash-reopen"]
    assert reopened.regulation_revision == 3
    assert reopened.pending_regulation_signal == "frustrated"
    assert not replay.privacy_marker_found
    assert first.durable_outcomes["out-of-scope-objective"].profile_proposals == 0
    assert first.durable_outcomes["self-correction"].observation_sequence == (
        "incorrect", "correct"
    )
    assert first.durable_outcomes["hint-exhaustion"].repair_calls == 3
    assert first.durable_outcomes["frustration"].intervention == "supportive-social"


def test_therapist_rating_fixture_is_separate_from_execution_classification_and_gated(tmp_path):
    scenario = next(item for item in load_scenarios(SCENARIOS) if item.scenario_id == "correct-answer")
    assert scenario.intervention_rating_fixture == "adequate"

    report = run_evaluation(
        (replace(scenario, intervention_rating_fixture="inadequate"),),
        database_path=tmp_path / "rating.db",
    )

    assert report.metrics.intervention_classifications == ("correct",)
    assert report.metrics.intervention_rating_fixtures == ("inadequate",)
    assert report.metrics.adequate_or_correctable_proportion == 0.0
    assert "intervention_adequacy_below_target" in report.hard_failures
    assert report.exit_code != 0


def test_scenario_rejects_unknown_intervention_rating_fixture(tmp_path):
    source = (SCENARIOS / "correct-answer.yaml").read_text().replace(
        "intervention_rating_fixture: adequate",
        "intervention_rating_fixture: excellent",
    )
    (tmp_path / "rating.yaml").write_text(source)

    with pytest.raises(EvalScenarioError, match="adequate, correctable, or inadequate"):
        load_scenarios(tmp_path)


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


def test_measured_latency_above_scenario_budget_is_a_hard_failure(tmp_path):
    scenario = next(item for item in load_scenarios(SCENARIOS) if item.scenario_id == "correct-answer")
    scenario = replace(
        scenario, expected=replace(scenario.expected, max_latency_ms=209)
    )
    report = run_evaluation((scenario,), database_path=tmp_path / "slow.db")
    assert report.metrics.latency_ms_p95 == 210
    assert "correct-answer.latency_budget_ms" in report.hard_failures


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


def test_unreviewed_regulation_speech_is_a_hard_failure(tmp_path):
    report = run_evaluation(
        load_scenarios(SCENARIOS),
        database_path=tmp_path / "unreviewed-regulation.db",
        faults=FaultAdapter(unreviewed_regulation_speech=True),
    )
    assert report.metrics.mathematical_speech_errors == 1
    assert "mathematical_speech_errors" in report.hard_failures
    assert "2 + 2 = 5" in " ".join(
        report.durable_outcomes["regulation-frustration"].released_speech
    )


def test_reviewed_speech_oracle_is_independent_of_production_renderer(tmp_path, monkeypatch):
    scenario = next(item for item in load_scenarios(SCENARIOS) if item.scenario_id == "regulation-frustration")
    monkeypatch.setattr(production_regulation, "canonical_regulation_speech", lambda *args, **kwargs: "Texto libre 2 + 2 = 5")

    report = run_evaluation((scenario,), database_path=tmp_path / "mutated-renderer.db")

    assert report.metrics.mathematical_speech_errors == 1
    assert "mathematical_speech_errors" in report.hard_failures


def test_concurrent_stale_mode_requires_exactly_two_turns(tmp_path):
    scenario = next(item for item in load_scenarios(SCENARIOS) if item.scenario_id == "regulation-stale-concurrency")

    with pytest.raises(EvalScenarioError, match="exactly two turns"):
        run_evaluation((replace(scenario, turns=scenario.turns[:1]),), database_path=tmp_path / "bad-stale.db")


def test_concurrent_stale_timeout_is_a_named_hard_failure(tmp_path, monkeypatch):
    import evals.math_tutor.runner as runner
    scenario = next(item for item in load_scenarios(SCENARIOS) if item.scenario_id == "regulation-stale-concurrency")
    monkeypatch.setattr(runner, "_CONCURRENT_WAIT_SECONDS", 0)

    report = run_evaluation((scenario,), database_path=tmp_path / "stale-timeout.db")

    assert "regulation-stale-concurrency.concurrent-stale-timeout" in report.hard_failures


def test_concurrent_stale_newer_model_deadlock_is_bounded(tmp_path, monkeypatch):
    import evals.math_tutor.runner as runner
    original = runner.ProductionFakeModelAdapter.complete

    async def block_newer(self, *, context, **kwargs):
        if context.current_turn.turn_id.endswith("-new"):
            await asyncio.Event().wait()
        return await original(self, context=context, **kwargs)

    monkeypatch.setattr(runner.ProductionFakeModelAdapter, "complete", block_newer)
    monkeypatch.setattr(runner, "_CONCURRENT_WAIT_SECONDS", 0.01)
    scenario = next(item for item in load_scenarios(SCENARIOS) if item.scenario_id == "regulation-stale-concurrency")
    newer = replace(scenario.turns[1], stt_confidence=.99)

    report = run_evaluation(
        (replace(scenario, turns=(scenario.turns[0], newer)),),
        database_path=tmp_path / "newer-deadlock.db",
    )

    assert "regulation-stale-concurrency.concurrent-stale-timeout" in report.hard_failures


def test_privacy_marker_in_actual_regulation_storage_is_a_hard_failure(tmp_path):
    report = run_evaluation(
        load_scenarios(SCENARIOS), database_path=tmp_path / "marker.db",
        faults=FaultAdapter(privacy_marker_storage=True),
    )

    assert report.durable_outcomes["regulation-replay-privacy"].privacy_marker_found
    assert report.metrics.diagnostic_or_privacy_violations == 1
    assert "diagnostic_or_privacy_violations" in report.hard_failures


def test_privacy_marker_in_captured_structured_log_is_a_hard_failure(tmp_path):
    report = run_evaluation(
        load_scenarios(SCENARIOS), database_path=tmp_path / "marker-log.db",
        faults=FaultAdapter(privacy_marker_log=True),
    )

    assert report.durable_outcomes["regulation-replay-privacy"].privacy_marker_found
    assert report.metrics.diagnostic_or_privacy_violations == 1

def test_generated_phone_and_email_leak_is_detected_without_diagnostic_words(tmp_path):
    report = run_evaluation(
        load_scenarios(SCENARIOS),
        database_path=tmp_path / "pii.db",
        faults=FaultAdapter(private_data_leak=True),
    )
    assert report.metrics.diagnostic_or_privacy_violations == 1
    assert "diagnostic_or_privacy_violations" in report.hard_failures


def test_eval_runner_never_uses_network_or_provider_secrets(tmp_path, monkeypatch):
    for key in ("OPENAI_API_KEY", "LLM_API_KEY", "STT_API_KEY", "TTS_API_KEY"):
        monkeypatch.setenv(key, "must-not-be-read")

    report = run_evaluation(
        load_scenarios(SCENARIOS), database_path=tmp_path / "offline.db"
    )

    assert report.exit_code == 0
    assert report.execution_mode == "offline-fake-model"
