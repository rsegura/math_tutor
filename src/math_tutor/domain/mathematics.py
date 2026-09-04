"""Exact verification for structured primary-mathematics answers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from math_tutor.domain.activities import (
    Activity,
    AnswerInputStatus,
    StructuredAnswer,
)
from math_tutor.domain.templates import ExpectedAnswerKind


class AnswerOutcome(Enum):
    """Deterministic outcome of checking one structured answer."""

    CORRECT = "correct"
    INCORRECT = "incorrect"
    AMBIGUOUS = "ambiguous"
    NOT_EVALUABLE = "not-evaluable"


@dataclass(frozen=True, slots=True)
class AnswerCheck:
    """Verification result with a stable machine-readable reason."""

    outcome: AnswerOutcome
    reason: str


def _has_valid_shape(answer: StructuredAnswer) -> bool:
    if answer.kind is ExpectedAnswerKind.INTEGER:
        return all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in answer.values.values()
        )
    if answer.kind is ExpectedAnswerKind.INTEGER_PAIR:
        return all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in answer.values.values()
        )
    if answer.kind is ExpectedAnswerKind.INTEGER_SEQUENCE:
        return all(
            isinstance(value, tuple)
            and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
            for value in answer.values.values()
        )
    if answer.kind is ExpectedAnswerKind.RELATION:
        return all(value in {"greater", "less", "equal"} for value in answer.values.values())
    return False


def verify_answer(activity: Activity, answer: StructuredAnswer) -> AnswerCheck:
    """Compare an interpreted answer exactly; no model or fuzzy parsing is used."""

    if answer.status is AnswerInputStatus.AMBIGUOUS:
        return AnswerCheck(AnswerOutcome.AMBIGUOUS, "answer-is-ambiguous")
    if answer.status is AnswerInputStatus.NOT_EVALUABLE:
        return AnswerCheck(AnswerOutcome.NOT_EVALUABLE, "answer-not-structured")
    expected = activity.expected_answer
    if answer.kind is not expected.kind:
        return AnswerCheck(AnswerOutcome.NOT_EVALUABLE, "answer-kind-does-not-match")
    if set(answer.values) != set(expected.values):
        return AnswerCheck(AnswerOutcome.NOT_EVALUABLE, "answer-fields-do-not-match")
    if not _has_valid_shape(answer):
        return AnswerCheck(AnswerOutcome.NOT_EVALUABLE, "answer-values-have-invalid-type")
    if dict(answer.values) == dict(expected.values):
        return AnswerCheck(AnswerOutcome.CORRECT, "answer-matches")
    return AnswerCheck(AnswerOutcome.INCORRECT, "answer-does-not-match")
