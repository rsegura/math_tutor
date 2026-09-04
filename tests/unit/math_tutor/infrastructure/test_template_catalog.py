"""Content-quality gates for the reviewed first vertical slice."""

from __future__ import annotations

from itertools import product
from pathlib import Path

from math_tutor.domain.templates import (
    ActivityTemplate,
    AnswerDerivation,
    ParameterBounds,
    ParameterRelationKind,
)
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
        assert all(
            isinstance(derivation, AnswerDerivation)
            for derivation in definition.expected_answer.derivations
        )
        assert {
            derivation.field
            for derivation in definition.expected_answer.derivations
        } == set(definition.expected_answer.fields)


def test_reviewed_relations_exclude_known_contradictory_parameter_sets() -> None:
    _, templates = loaded_catalogs()

    greater = templates.template("compare-choose-greater")
    assert any(
        relation.kind is ParameterRelationKind.DISTINCT
        for relation in greater.parameter_relations
    )
    assert not greater.allows_parameter_values({"left": 7, "right": 7})

    units = templates.template("decompose-find-units")
    assert not units.allows_parameter_values({"number": 42, "tens": 7})
    assert units.allows_parameter_values({"number": 42, "tens": 4})

    missing_mark = templates.template("number-line-missing-mark")
    assert not missing_mark.allows_parameter_values({"before": 3, "after": 9})
    assert missing_mark.allows_parameter_values({"before": 3, "after": 5})

    expanded = templates.template("compose-from-expanded-values")
    assert not expanded.allows_parameter_values({"tens_value": 25, "units": 3})
    assert expanded.allows_parameter_values({"tens_value": 20, "units": 3})


def test_every_reviewed_template_has_at_least_one_coherent_parameter_set() -> None:
    _, templates = loaded_catalogs()

    for definition in templates.templates:
        names = tuple(definition.parameter_bounds)
        value_ranges = (
            range(bounds.minimum, bounds.maximum + 1)
            for bounds in definition.parameter_bounds.values()
        )
        assert any(
            definition.allows_parameter_values(
                dict(zip(names, values, strict=True))
            )
            for values in product(*value_ranges)
        ), definition.id


def test_every_allowed_combination_respects_reviewed_coherence_rules() -> None:
    _, templates = loaded_catalogs()

    checks = {
        "compare-choose-greater": lambda values: (
            values["left"] != values["right"]
        ),
        "compare-choose-smaller": lambda values: (
            values["left"] != values["right"]
        ),
        "compose-from-expanded-values": lambda values: (
            values["tens_value"] % 10 == 0
        ),
        "decompose-find-units": lambda values: (
            values["tens"] == values["number"] // 10
        ),
        "number-line-missing-mark": lambda values: (
            values["after"] == values["before"] + 2
        ),
    }
    for template_id, check in checks.items():
        definition = templates.template(template_id)
        names = tuple(definition.parameter_bounds)
        ranges = (
            range(bounds.minimum, bounds.maximum + 1)
            for bounds in definition.parameter_bounds.values()
        )
        for combination in product(*ranges):
            values = dict(zip(names, combination, strict=True))
            if definition.allows_parameter_values(values):
                assert check(values), (template_id, values)


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
