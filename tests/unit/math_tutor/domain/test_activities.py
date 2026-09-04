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
