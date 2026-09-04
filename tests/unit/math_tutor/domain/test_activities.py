"""Deterministic generation from reviewed activity templates."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from math_tutor.domain.activities import (
    InvalidActivity,
    StructuredAnswer,
    generate_activity,
)
from math_tutor.domain.templates import ActivityTemplate
from math_tutor.domain.templates import (
    ActivityFamily,
    AnswerConstraint,
    AnswerDerivation,
    AnswerDerivationOperation,
    ExpectedAnswerKind,
    ExpectedAnswerSpec,
    ParameterBounds,
    ParameterRelationKind,
)
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs


ROOT = Path(__file__).parents[4]


@pytest.fixture(scope="module")
def catalogs():
    return load_curriculum_catalogs(
        ROOT / "src/math_tutor/curricula/primary-math-v1.yaml",
        ROOT / "src/math_tutor/curricula/activity-templates-v1.yaml",
    )


def test_generation_api_consumes_the_canonical_template_type() -> None:
    annotation = inspect.signature(generate_activity).parameters["template"].annotation

    assert annotation == "ActivityTemplate"


def test_activity_generation_is_deterministic_for_template_seed_and_difficulty(
    catalogs,
) -> None:
    _, templates = catalogs
    template = templates.template("add-within-20-bridge-ten")

    first = generate_activity(template, seed=729, difficulty=4)
    second = generate_activity(template, seed=729, difficulty=4)

    assert first == second
    assert first.prompt_es == template.prompt_template_es.format(
        **first.parameters
    )


def test_generation_rejects_a_difficulty_outside_the_reviewed_range(catalogs) -> None:
    _, templates = catalogs
    template = templates.template("add-within-20-bridge-ten")

    with pytest.raises(InvalidActivity, match="difficulty"):
        generate_activity(template, seed=1, difficulty=1)


def test_every_reviewed_template_generates_an_activity_inside_its_contract(
    catalogs,
) -> None:
    curriculum, templates = catalogs

    for template in templates.templates:
        for difficulty in range(template.difficulty_min, template.difficulty_max + 1):
            activity = generate_activity(template, seed=20260904, difficulty=difficulty)
            objective = curriculum.objective(template.objective_id)

            assert activity.template_id == template.id
            assert activity.objective_id == template.objective_id
            assert activity.error_pattern_ids == template.error_pattern_ids
            assert activity.hint_ids == template.hint_ids
            assert template.allows_parameter_values(activity.parameters)
            assert activity.expected_answer.kind is template.expected_answer.kind
            assert tuple(activity.expected_answer.values) == template.expected_answer.fields
            assert set(activity.error_pattern_ids) <= set(
                objective.known_error_pattern_ids
            )
            assert set(activity.hint_ids) <= {hint.id for hint in objective.hints}


def test_generated_activity_and_answer_are_immutable(catalogs) -> None:
    _, templates = catalogs
    activity = generate_activity(
        templates.template("place-value-name-both"), seed=7, difficulty=1
    )

    with pytest.raises(TypeError):
        activity.parameters["number"] = 23  # type: ignore[index]
    with pytest.raises(TypeError):
        activity.expected_answer.values["tens"] = 2  # type: ignore[index]
    assert isinstance(activity.expected_answer, StructuredAnswer)


def test_subtraction_generation_never_emits_a_negative_expected_answer(catalogs) -> None:
    _, templates = catalogs

    for template in templates.for_objective("subtract-within-20"):
        for seed in range(50):
            activity = generate_activity(
                template, seed=seed, difficulty=template.difficulty_max
            )
            assert activity.expected_answer.values["answer"] >= 0


def test_difficulty_partitions_the_reviewed_candidate_space(catalogs) -> None:
    _, templates = catalogs
    template = templates.template("add-within-20-bridge-ten")

    easy = generate_activity(template, seed=11, difficulty=3)
    hard = generate_activity(template, seed=11, difficulty=5)

    assert sum(easy.parameters.values()) <= sum(hard.parameters.values())


def _fixed_template(
    operation: AnswerDerivationOperation,
    parameter_values: dict[str, int],
    expected_kind: ExpectedAnswerKind,
    *,
    constraints: tuple[AnswerConstraint, ...] = (),
) -> ActivityTemplate:
    field = "values" if expected_kind is ExpectedAnswerKind.INTEGER_SEQUENCE else (
        "relation" if expected_kind is ExpectedAnswerKind.RELATION else "answer"
    )
    placeholders = " ".join(f"{{{name}}}" for name in parameter_values)
    return ActivityTemplate(
        id=f"exercise-{operation.value}",
        objective_id="reviewed-objective",
        family=ActivityFamily.ADDITION,
        parameter_bounds={
            name: ParameterBounds(value, value)
            for name, value in parameter_values.items()
        },
        difficulty_min=1,
        difficulty_max=1,
        prompt_template_es=placeholders,
        expected_answer=ExpectedAnswerSpec(
            kind=expected_kind,
            fields=(field,),
            constraints=constraints,
            derivations=(AnswerDerivation(field, operation, tuple(parameter_values)),),
        ),
        parameter_relations=(),
        error_pattern_ids=("reviewed-error",),
        hint_ids=("reviewed-hint",),
    )


@pytest.mark.parametrize(
    ("operation", "parameters", "kind", "expected"),
    [
        (AnswerDerivationOperation.VALUE, {"number": 8}, ExpectedAnswerKind.INTEGER, 8),
        (AnswerDerivationOperation.SUM, {"a": 2, "b": 3, "c": 4}, ExpectedAnswerKind.INTEGER, 9),
        (AnswerDerivationOperation.DIFFERENCE, {"a": 9, "b": 4}, ExpectedAnswerKind.INTEGER, 5),
        (AnswerDerivationOperation.DOUBLE, {"number": 6}, ExpectedAnswerKind.INTEGER, 12),
        (AnswerDerivationOperation.MINIMUM, {"a": 7, "b": 3}, ExpectedAnswerKind.INTEGER, 3),
        (AnswerDerivationOperation.MAXIMUM, {"a": 7, "b": 3}, ExpectedAnswerKind.INTEGER, 7),
        (AnswerDerivationOperation.RELATION, {"a": 7, "b": 3}, ExpectedAnswerKind.RELATION, "greater"),
        (AnswerDerivationOperation.SUCCESSOR, {"number": 8}, ExpectedAnswerKind.INTEGER, 9),
        (AnswerDerivationOperation.PREDECESSOR, {"number": 8}, ExpectedAnswerKind.INTEGER, 7),
        (AnswerDerivationOperation.FOLLOWING_SEQUENCE, {"start": 4, "length": 3}, ExpectedAnswerKind.INTEGER_SEQUENCE, (5, 6, 7)),
        (AnswerDerivationOperation.TENS_DIGIT, {"number": 47}, ExpectedAnswerKind.INTEGER, 4),
        (AnswerDerivationOperation.UNITS_DIGIT, {"number": 47}, ExpectedAnswerKind.INTEGER, 7),
        (AnswerDerivationOperation.TENS_VALUE, {"number": 47}, ExpectedAnswerKind.INTEGER, 40),
        (AnswerDerivationOperation.COMPOSE_TENS_UNITS, {"tens": 4, "units": 7}, ExpectedAnswerKind.INTEGER, 47),
    ],
)
def test_each_reviewed_derivation_has_known_independent_examples(
    operation, parameters, kind, expected
) -> None:
    activity = generate_activity(
        _fixed_template(operation, parameters, kind), seed=1, difficulty=1
    )

    assert next(iter(activity.expected_answer.values.values())) == expected


def _oracle_derivation(operation, values):
    if operation is AnswerDerivationOperation.VALUE:
        return values[0]
    if operation is AnswerDerivationOperation.SUM:
        return sum(values)
    if operation is AnswerDerivationOperation.DIFFERENCE:
        return values[0] - values[1]
    if operation is AnswerDerivationOperation.DOUBLE:
        return values[0] + values[0]
    if operation is AnswerDerivationOperation.MINIMUM:
        return sorted(values)[0]
    if operation is AnswerDerivationOperation.MAXIMUM:
        return sorted(values)[-1]
    if operation is AnswerDerivationOperation.RELATION:
        return "greater" if values[0] > values[1] else "less" if values[0] < values[1] else "equal"
    if operation is AnswerDerivationOperation.SUCCESSOR:
        return values[0] + 1
    if operation is AnswerDerivationOperation.PREDECESSOR:
        return values[0] - 1
    if operation is AnswerDerivationOperation.FOLLOWING_SEQUENCE:
        return tuple(values[0] + offset for offset in range(1, values[1] + 1))
    if operation is AnswerDerivationOperation.TENS_DIGIT:
        return divmod(values[0], 10)[0]
    if operation is AnswerDerivationOperation.UNITS_DIGIT:
        return divmod(values[0], 10)[1]
    if operation is AnswerDerivationOperation.TENS_VALUE:
        return divmod(values[0], 10)[0] * 10
    if operation is AnswerDerivationOperation.COMPOSE_TENS_UNITS:
        return values[0] * 10 + values[1]
    raise AssertionError(f"missing test oracle for {operation}")


def _oracle_two_digit(template, activity) -> bool:
    integers = [value for value in activity.expected_answer.values.values() if isinstance(value, int)]
    if any(10 <= value <= 99 for value in integers):
        return True
    referenced = [
        activity.parameters[name]
        for derivation in template.expected_answer.derivations
        for name in derivation.parameters
    ]
    if any(10 <= value <= 99 for value in referenced):
        return True
    derivations = template.expected_answer.derivations
    return (
        len(derivations) == 2
        and all(item.operation is AnswerDerivationOperation.VALUE for item in derivations)
        and 1 <= referenced[0] <= 9
        and 0 <= referenced[1] <= 9
    )


def _oracle_relation(relation, parameters) -> bool:
    values = tuple(parameters[name] for name in relation.parameters)
    if relation.kind is ParameterRelationKind.DISTINCT:
        return values[0] != values[1]
    if relation.kind is ParameterRelationKind.EQUALS_TENS_DIGIT:
        return values[0] == values[1] // 10
    if relation.kind is ParameterRelationKind.OFFSET_EQUALS:
        return values[0] == values[1] + relation.value
    if relation.kind is ParameterRelationKind.MULTIPLE_OF:
        return values[0] % relation.value == 0
    raise AssertionError(f"missing test oracle for {relation.kind}")


def test_catalog_corpus_matches_an_independent_math_and_constraint_oracle(catalogs) -> None:
    _, templates = catalogs
    assert len(templates.templates) == 34

    for template in templates.templates:
        for difficulty in range(template.difficulty_min, template.difficulty_max + 1):
            for seed in (0, 1, 17, 101):
                activity = generate_activity(template, seed=seed, difficulty=difficulty)
                for name, value in activity.parameters.items():
                    bounds = template.parameter_bounds[name]
                    assert bounds.minimum <= value <= bounds.maximum
                for derivation in template.expected_answer.derivations:
                    operands = tuple(activity.parameters[name] for name in derivation.parameters)
                    assert activity.expected_answer.values[derivation.field] == _oracle_derivation(derivation.operation, operands)

                flat = []
                for value in activity.expected_answer.values.values():
                    flat.extend(value if isinstance(value, tuple) else (value,) if isinstance(value, int) else ())
                contextual = flat or [
                    activity.parameters[name]
                    for derivation in template.expected_answer.derivations
                    for name in derivation.parameters
                ]
                constraints = set(template.expected_answer.constraints)
                if AnswerConstraint.NON_NEGATIVE in constraints:
                    assert all(value >= 0 for value in contextual)
                if AnswerConstraint.WITHIN_10 in constraints:
                    assert all(0 <= value <= 10 for value in contextual)
                if AnswerConstraint.WITHIN_20 in constraints:
                    assert all(0 <= value <= 20 for value in contextual)
                if AnswerConstraint.TWO_DIGIT in constraints:
                    assert _oracle_two_digit(template, activity)
                if AnswerConstraint.NO_CARRY in constraints:
                    sums = [
                        derivation
                        for derivation in template.expected_answer.derivations
                        if derivation.operation is AnswerDerivationOperation.SUM
                    ]
                    assert sums
                    assert all(
                        sum(activity.parameters[name] % 10 for name in item.parameters) <= 9
                        for item in sums
                    )
                if AnswerConstraint.NO_BORROW in constraints:
                    differences = [
                        derivation
                        for derivation in template.expected_answer.derivations
                        if derivation.operation is AnswerDerivationOperation.DIFFERENCE
                    ]
                    assert differences
                    assert all(
                        activity.parameters[item.parameters[0]] % 10
                        >= activity.parameters[item.parameters[1]] % 10
                        for item in differences
                    )
                for relation in template.parameter_relations:
                    assert _oracle_relation(relation, activity.parameters)


def test_two_digit_rejects_a_contract_without_a_two_digit_quantity() -> None:
    template = _fixed_template(
        AnswerDerivationOperation.VALUE,
        {"number": 7},
        ExpectedAnswerKind.INTEGER,
        constraints=(AnswerConstraint.TWO_DIGIT,),
    )

    with pytest.raises(InvalidActivity, match="no mathematically valid"):
        generate_activity(template, seed=1, difficulty=1)


def test_no_carry_filters_assignments_using_operand_units() -> None:
    template = _fixed_template(
        AnswerDerivationOperation.SUM,
        {"left": 17, "right": 6},
        ExpectedAnswerKind.INTEGER,
        constraints=(AnswerConstraint.NO_CARRY,),
    )

    with pytest.raises(InvalidActivity, match="no mathematically valid"):
        generate_activity(template, seed=1, difficulty=1)


def test_no_borrow_filters_assignments_using_operand_units() -> None:
    template = _fixed_template(
        AnswerDerivationOperation.DIFFERENCE,
        {"minuend": 12, "subtrahend": 8},
        ExpectedAnswerKind.INTEGER,
        constraints=(AnswerConstraint.NON_NEGATIVE, AnswerConstraint.NO_BORROW),
    )

    with pytest.raises(InvalidActivity, match="no mathematically valid"):
        generate_activity(template, seed=1, difficulty=1)


@pytest.mark.parametrize(
    ("operation", "parameters", "constraint", "expected"),
    [
        (AnswerDerivationOperation.SUM, {"left": 12, "right": 7}, AnswerConstraint.NO_CARRY, 19),
        (AnswerDerivationOperation.DIFFERENCE, {"minuend": 18, "subtrahend": 6}, AnswerConstraint.NO_BORROW, 12),
    ],
)
def test_column_constraints_accept_assignments_that_need_no_regrouping(
    operation, parameters, constraint, expected
) -> None:
    activity = generate_activity(
        _fixed_template(
            operation,
            parameters,
            ExpectedAnswerKind.INTEGER,
            constraints=(constraint,),
        ),
        seed=1,
        difficulty=1,
    )

    assert activity.expected_answer.values["answer"] == expected


@pytest.mark.parametrize("constraint", [AnswerConstraint.NO_CARRY, AnswerConstraint.NO_BORROW])
def test_column_constraints_fail_closed_without_a_matching_derivation(
    constraint,
) -> None:
    template = _fixed_template(
        AnswerDerivationOperation.VALUE,
        {"number": 8},
        ExpectedAnswerKind.INTEGER,
        constraints=(constraint,),
    )

    with pytest.raises(InvalidActivity, match="no mathematically valid"):
        generate_activity(template, seed=1, difficulty=1)


def test_non_negative_filters_negative_result_even_when_parameters_are_in_bounds() -> None:
    template = _fixed_template(
        AnswerDerivationOperation.DIFFERENCE,
        {"minuend": 2, "subtrahend": 8},
        ExpectedAnswerKind.INTEGER,
        constraints=(AnswerConstraint.NON_NEGATIVE,),
    )

    with pytest.raises(InvalidActivity, match="no mathematically valid"):
        generate_activity(template, seed=1, difficulty=1)


def test_generator_fails_closed_for_an_unrecognised_constraint() -> None:
    template = _fixed_template(
        AnswerDerivationOperation.VALUE,
        {"number": 8},
        ExpectedAnswerKind.INTEGER,
    )
    object.__setattr__(template.expected_answer, "constraints", (object(),))

    with pytest.raises(InvalidActivity, match="unsupported answer constraint"):
        generate_activity(template, seed=1, difficulty=1)
