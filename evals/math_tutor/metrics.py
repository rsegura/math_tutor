"""Stable metric contracts for the offline tutoring acceptance gate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


HARD_METRICS = (
    "mathematical_speech_errors",
    "unsupported_profile_updates",
    "stt_misattributions",
    "ignored_stops",
    "diagnostic_or_privacy_violations",
)


@dataclass(frozen=True, slots=True)
class EvalMetrics:
    mathematical_speech_errors: int
    unsupported_profile_updates: int
    stt_misattributions: int
    ignored_stops: int
    diagnostic_or_privacy_violations: int
    intervention_classifications: tuple[str, ...]
    intervention_rating_fixtures: tuple[str, ...]
    adequate_or_correctable_proportion: float
    intervention_adequacy_target: float
    evidence_coverage: float
    latency_ms_p95: int
    review_fixture_duration_seconds: int


@dataclass(frozen=True, slots=True)
class EvalReport:
    scenarios_run: int
    metrics: EvalMetrics
    hard_failures: tuple[str, ...]
    exit_code: int
    durable_outcomes: Mapping[str, DurableOutcome]
    execution_mode: str = "offline-fake-model"


@dataclass(frozen=True, slots=True)
class DurableOutcome:
    observations: int
    correct: int
    incorrect: int
    ambiguous: int
    not_evaluable: int
    evidence_count: int
    profile_proposals: int
    attempts_used: int
    hints_used: int
    consecutive_correct: int
    consecutive_incorrect: int
    observation_sequence: tuple[str, ...]
    repair_calls: int
    decisions: int
    intervention: str
    latency_ms: int
    released_speech: tuple[str, ...]
    model_artifacts: tuple[str, ...]
    terminal: bool
