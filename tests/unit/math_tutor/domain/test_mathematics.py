"""Deterministic verification of already-structured mathematical answers."""

from __future__ import annotations

from pathlib import Path

import pytest

from math_tutor.domain.activities import StructuredAnswer, generate_activity
from math_tutor.domain.mathematics import AnswerOutcome, verify_answer
from math_tutor.domain.templates import ExpectedAnswerKind
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs


ROOT = Path(__file__).parents[4]


@pytest.fixture(scope="module")
def templates():
    return load_curriculum_catalogs(
        ROOT / "src/math_tutor/curricula/primary-math-v1.yaml",
        ROOT / "src/math_tutor/curricula/activity-templates-v1.yaml",
    )[1]


def test_place_value_answer_is_checked_without_the_llm(templates) -> None:
    activity = generate_activity(
        templates.template("place-value-name-both"), seed=7, difficulty=1
    )

    result = verify_answer(activity, activity.expected_answer)

    assert result.outcome is AnswerOutcome.CORRECT


def test_exact_place_value_rejects_swapped_tens_and_units(templates) -> None:
    activity = generate_activity(
        templates.template("place-value-name-both"), seed=8, difficulty=1
    )
    expected = activity.expected_answer.values
    swapped = StructuredAnswer.evaluable(
        ExpectedAnswerKind.INTEGER_PAIR,
        {"tens": expected["units"], "units": expected["tens"]},
    )

    result = verify_answer(activity, swapped)

    assert result.outcome is AnswerOutcome.INCORRECT


def test_answer_field_mapping_order_does_not_change_the_result(templates) -> None:
    activity = generate_activity(
        templates.template("place-value-name-both"), seed=8, difficulty=1
    )
    expected = activity.expected_answer.values
    reversed_fields = StructuredAnswer.evaluable(
        ExpectedAnswerKind.INTEGER_PAIR,
        {"units": expected["units"], "tens": expected["tens"]},
    )

    assert verify_answer(activity, reversed_fields).outcome is AnswerOutcome.CORRECT


def test_addition_is_checked_against_the_bounded_expected_total(templates) -> None:
    activity = generate_activity(
        templates.template("add-within-20-join-groups"), seed=4, difficulty=3
    )
    wrong = StructuredAnswer.evaluable(
        ExpectedAnswerKind.INTEGER, {"answer": 21}
    )

    assert verify_answer(activity, wrong).outcome is AnswerOutcome.INCORRECT


def test_structurally_invalid_answer_is_not_evaluable(templates) -> None:
    activity = generate_activity(
        templates.template("add-small-double"), seed=4, difficulty=2
    )
    missing_field = StructuredAnswer.evaluable(
        ExpectedAnswerKind.INTEGER, {"total": 8}
    )

    result = verify_answer(activity, missing_field)

    assert result.outcome is AnswerOutcome.NOT_EVALUABLE
    assert result.reason == "answer-fields-do-not-match"


def test_interpreter_can_mark_an_answer_as_ambiguous(templates) -> None:
    activity = generate_activity(
        templates.template("add-small-double"), seed=4, difficulty=2
    )

    result = verify_answer(activity, StructuredAnswer.ambiguous())

    assert result.outcome is AnswerOutcome.AMBIGUOUS


def test_interpreter_can_mark_an_answer_as_not_evaluable(templates) -> None:
    activity = generate_activity(
        templates.template("add-small-double"), seed=4, difficulty=2
    )

    result = verify_answer(activity, StructuredAnswer.not_evaluable())

    assert result.outcome is AnswerOutcome.NOT_EVALUABLE


def test_relation_answers_use_a_closed_vocabulary(templates) -> None:
    activity = generate_activity(
        templates.template("compare-name-relation"), seed=2, difficulty=2
    )
    invalid = StructuredAnswer.evaluable(
        ExpectedAnswerKind.RELATION, {"relation": "aproximadamente"}
    )

    assert verify_answer(activity, invalid).outcome is AnswerOutcome.NOT_EVALUABLE


def test_boolean_is_not_accepted_as_an_integer_answer(templates) -> None:
    activity = generate_activity(
        templates.template("add-small-double"), seed=4, difficulty=2
    )
    boolean = StructuredAnswer.evaluable(
        ExpectedAnswerKind.INTEGER, {"answer": True}
    )

    assert verify_answer(activity, boolean).outcome is AnswerOutcome.NOT_EVALUABLE
