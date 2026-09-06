"""Stable metric contracts for the offline tutoring acceptance gate."""

from __future__ import annotations

from dataclasses import dataclass


HARD_METRICS = (
    "mathematical_speech_errors",
    "unsupported_profile_updates",
    "stt_misattributions",
    "ignored_stops",
)


@dataclass(frozen=True, slots=True)
class EvalMetrics:
    mathematical_speech_errors: int
    unsupported_profile_updates: int
    stt_misattributions: int
    ignored_stops: int
    intervention_ratings: tuple[str, ...]
    evidence_coverage: float
    latency_ms_p95: int
    review_time_seconds: int


@dataclass(frozen=True, slots=True)
class EvalReport:
    scenarios_run: int
    metrics: EvalMetrics
    hard_failures: tuple[str, ...]
    exit_code: int
    execution_mode: str = "offline-fake-model"

