"""Content-quality gates for the reviewed first vertical slice."""

from __future__ import annotations

from pathlib import Path

from math_tutor.domain.templates import ActivityTemplate, ParameterBounds
from math_tutor.infrastructure.curriculum_loader import load_curriculum_catalogs


ROOT = Path(__file__).resolve().parents[4]
CURRICULUM_PATH = ROOT / "src/math_tutor/curricula/primary-math-v1.yaml"
TEMPLATES_PATH = ROOT / "src/math_tutor/curricula/activity-templates-v1.yaml"


def loaded_catalogs():
    return load_curriculum_catalogs(CURRICULUM_PATH, TEMPLATES_PATH)


def test_reviewed_catalog_has_between_thirty_and_fifty_templates() -> None:
    _, templates = loaded_catalogs()

    assert 30 <= len(templates.templates) <= 50


def test_every_vertical_slice_objective_has_template_coverage() -> None:
    curriculum, templates = loaded_catalogs()

    assert {
        item.objective_id for item in templates.templates
    } == {objective.id for objective in curriculum.objectives}


def test_every_template_reference_resolves_within_its_objective() -> None:
    curriculum, templates = loaded_catalogs()

    for definition in templates.templates:
        objective = curriculum.objective(definition.objective_id)
        assert definition.family.value in objective.activity_family_ids
        assert set(definition.error_pattern_ids) <= set(
            objective.known_error_pattern_ids
        )
        assert set(definition.hint_ids) <= {
            hint.id for hint in objective.hints
        }


def test_every_catalog_entry_is_typed_and_parameterised() -> None:
    _, templates = loaded_catalogs()

    for definition in templates.templates:
        assert isinstance(definition, ActivityTemplate)
        assert definition.parameter_bounds
        assert all(
            isinstance(bounds, ParameterBounds)
            for bounds in definition.parameter_bounds.values()
        )
        assert "{" in definition.prompt_template_es


def test_reviewed_templates_are_not_duplicates_with_renamed_ids() -> None:
    _, templates = loaded_catalogs()
    signatures = {
        (
            item.objective_id,
            item.family,
            item.prompt_template_es,
            tuple(item.parameter_bounds.items()),
            item.expected_answer,
        )
        for item in templates.templates
    }

    assert len(signatures) == len(templates.templates)


def test_loaded_catalog_order_is_lexical_and_deterministic() -> None:
    _, first = loaded_catalogs()
    _, second = loaded_catalogs()
    first_ids = tuple(item.id for item in first.templates)

    assert first_ids == tuple(sorted(first_ids))
    assert first.templates == second.templates


def test_an_objective_with_two_prerequisites_requires_all_of_them() -> None:
    curriculum, _ = loaded_catalogs()
    objective = curriculum.objective("add-within-20")

    assert len(objective.prerequisite_ids) == 2
    assert not curriculum.prerequisites_met(
        objective.id, {objective.prerequisite_ids[0]}
    )
    assert curriculum.prerequisites_met(
        objective.id, set(objective.prerequisite_ids)
    )
