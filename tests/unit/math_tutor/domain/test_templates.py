"""Tests for the canonical reviewed activity-template model."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from math_tutor.domain.templates import (
    ActivityFamily,
    ActivityTemplate,
    ActivityTemplateCatalog,
    AnswerConstraint,
    ExpectedAnswerKind,
    ExpectedAnswerSpec,
    InvalidActivityTemplate,
    ParameterBounds,
)


def expected_answer() -> ExpectedAnswerSpec:
    return ExpectedAnswerSpec(
        kind=ExpectedAnswerKind.INTEGER,
        fields=("answer",),
        constraints=(AnswerConstraint.WITHIN_20,),
    )


def template(
    template_id: str = "add-two-groups",
    *,
    objective_id: str = "add-within-20",
    family: ActivityFamily = ActivityFamily.ADDITION,
    parameters: object | None = None,
    difficulty_min: object = 1,
    difficulty_max: object = 3,
    prompt: str = "Suma {left} y {right}.",
    errors: object = ("adds-one-extra",),
    hints: object = ("join-groups",),
) -> ActivityTemplate:
    return ActivityTemplate(
        id=template_id,
        objective_id=objective_id,
        family=family,
        parameter_bounds=(
            {
                "left": ParameterBounds(minimum=1, maximum=10),
                "right": ParameterBounds(minimum=1, maximum=10),
            }
            if parameters is None
            else parameters
        ),
        difficulty_min=difficulty_min,
        difficulty_max=difficulty_max,
        prompt_template_es=prompt,
        expected_answer=expected_answer(),
        error_pattern_ids=errors,
        hint_ids=hints,
    )  # type: ignore[arg-type]


def test_activity_families_cover_the_first_vertical_slice() -> None:
    assert {family.value for family in ActivityFamily} == {
        "counting",
        "number-sequence",
        "comparison",
        "place-value",
        "composition",
        "decomposition",
        "number-line",
        "addition",
        "subtraction",
    }


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [(True, 2), (1, False), (1.0, 2), (1, "2")],
)
def test_parameter_bounds_require_integer_limits(
    minimum: object, maximum: object
) -> None:
    with pytest.raises(InvalidActivityTemplate, match="integer"):
        ParameterBounds(minimum=minimum, maximum=maximum)  # type: ignore[arg-type]


def test_parameter_bounds_reject_an_inverted_range() -> None:
    with pytest.raises(InvalidActivityTemplate, match="minimum.*maximum"):
        ParameterBounds(minimum=4, maximum=3)


def test_parameter_bounds_are_immutable() -> None:
    bounds = ParameterBounds(minimum=1, maximum=3)

    with pytest.raises(FrozenInstanceError):
        bounds.maximum = 4  # type: ignore[misc]


def test_expected_answer_requires_a_typed_kind() -> None:
    with pytest.raises(InvalidActivityTemplate, match="kind"):
        ExpectedAnswerSpec(
            kind="integer",  # type: ignore[arg-type]
            fields=("answer",),
            constraints=(AnswerConstraint.WITHIN_20,),
        )


@pytest.mark.parametrize("field", ["fields", "constraints"])
def test_expected_answer_rejects_a_scalar_string_for_sequences(field: str) -> None:
    values = {
        "kind": ExpectedAnswerKind.INTEGER,
        "fields": ("answer",),
        "constraints": (AnswerConstraint.WITHIN_20,),
        field: "not-a-sequence",
    }

    with pytest.raises(InvalidActivityTemplate, match=field):
        ExpectedAnswerSpec(**values)  # type: ignore[arg-type]


def test_expected_answer_requires_nonempty_unique_fields() -> None:
    with pytest.raises(InvalidActivityTemplate, match="fields"):
        ExpectedAnswerSpec(
            kind=ExpectedAnswerKind.INTEGER,
            fields=("answer", "answer"),
            constraints=(AnswerConstraint.WITHIN_20,),
        )


def test_expected_answer_requires_typed_constraints() -> None:
    with pytest.raises(InvalidActivityTemplate, match="constraint"):
        ExpectedAnswerSpec(
            kind=ExpectedAnswerKind.INTEGER,
            fields=("answer",),
            constraints=("within-20",),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("field", ["id", "objective_id", "prompt"])
@pytest.mark.parametrize("bad_value", ["", " ", " leading", "trailing "])
def test_template_requires_trimmed_nonempty_identity_and_spanish_prompt(
    field: str, bad_value: str
) -> None:
    argument_name = "template_id" if field == "id" else field
    arguments = {argument_name: bad_value}

    with pytest.raises(InvalidActivityTemplate, match=field.replace("_", " ")):
        template(**arguments)  # type: ignore[arg-type]


def test_template_requires_a_typed_activity_family() -> None:
    with pytest.raises(InvalidActivityTemplate, match="family"):
        template(family="addition")  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["template_id", "objective_id"])
def test_template_rejects_an_identifier_that_is_not_stable(field: str) -> None:
    with pytest.raises(InvalidActivityTemplate, match="stable"):
        template(**{field: "Not stable"})  # type: ignore[arg-type]


def test_template_requires_at_least_one_parameter() -> None:
    with pytest.raises(InvalidActivityTemplate, match="parameter"):
        template(parameters={})


def test_template_requires_prompt_placeholders_to_match_parameters() -> None:
    with pytest.raises(InvalidActivityTemplate, match="placeholder"):
        template(prompt="Suma los dos números.")


def test_template_rejects_untyped_parameter_bounds() -> None:
    with pytest.raises(InvalidActivityTemplate, match="ParameterBounds"):
        template(parameters={"left": {"min": 1, "max": 3}})


def test_template_copies_parameter_bounds_into_an_immutable_mapping() -> None:
    source = {"left": ParameterBounds(minimum=1, maximum=3)}
    definition = template(parameters=source, prompt="Usa {left}.")

    source["right"] = ParameterBounds(minimum=1, maximum=3)

    assert tuple(definition.parameter_bounds) == ("left",)
    with pytest.raises(TypeError):
        definition.parameter_bounds["other"] = ParameterBounds(1, 2)  # type: ignore[index]


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [(True, 2), (1, False), (0, 2), (1, 6), (4, 3)],
)
def test_template_rejects_invalid_difficulty_bounds(
    minimum: object, maximum: object
) -> None:
    with pytest.raises(InvalidActivityTemplate, match="difficulty"):
        template(difficulty_min=minimum, difficulty_max=maximum)


@pytest.mark.parametrize("field", ["errors", "hints"])
def test_template_rejects_a_scalar_string_for_reference_sequences(field: str) -> None:
    with pytest.raises(InvalidActivityTemplate, match=field[:-1]):
        template(**{field: "one-id"})  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["errors", "hints"])
def test_template_requires_nonempty_reference_sequences(field: str) -> None:
    with pytest.raises(InvalidActivityTemplate, match=field[:-1]):
        template(**{field: ()})  # type: ignore[arg-type]


def test_template_is_immutable() -> None:
    definition = template()

    with pytest.raises(FrozenInstanceError):
        definition.id = "changed"  # type: ignore[misc]


def test_catalog_rejects_duplicate_template_ids() -> None:
    with pytest.raises(InvalidActivityTemplate, match="duplicate template"):
        ActivityTemplateCatalog((template(), template()))


def test_catalog_orders_templates_deterministically_by_id() -> None:
    catalog = ActivityTemplateCatalog(
        (template("z-template"), template("a-template"))
    )

    assert tuple(item.id for item in catalog.templates) == (
        "a-template",
        "z-template",
    )


def test_catalog_returns_a_template_by_id() -> None:
    expected = template()
    catalog = ActivityTemplateCatalog((expected,))

    assert catalog.template(expected.id) is expected


def test_catalog_returns_objective_templates_in_deterministic_order() -> None:
    catalog = ActivityTemplateCatalog(
        (
            template("z-addition"),
            template("other", objective_id="units-tens"),
            template("a-addition"),
        )
    )

    assert tuple(item.id for item in catalog.for_objective("add-within-20")) == (
        "a-addition",
        "z-addition",
    )


def test_catalog_never_exposes_raw_dictionary_entries() -> None:
    catalog = ActivityTemplateCatalog((template(),))

    assert all(isinstance(item, ActivityTemplate) for item in catalog.templates)
