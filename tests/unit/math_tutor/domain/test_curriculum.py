"""Tests for the immutable curriculum competency graph."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from math_tutor.domain.curriculum import (
    CurriculumBand,
    CurriculumCatalog,
    HintDefinition,
    InvalidCurriculum,
    LearningObjective,
)


def hint(hint_id: str = "count-units", order: int = 1) -> HintDefinition:
    return HintDefinition(
        id=hint_id,
        text="Cuenta primero las unidades.",
        order=order,
    )


def objective(
    objective_id: str,
    *,
    band: CurriculumBand = CurriculumBand.INITIAL,
    title: str = "Unidades y decenas",
    instruction: str = "Distingue las unidades de las decenas.",
    requires: tuple[str, ...] = (),
    families: tuple[str, ...] = ("oral-place-value",),
    errors: tuple[str, ...] = (),
    hints: tuple[HintDefinition, ...] = (hint(),),
) -> LearningObjective:
    return LearningObjective(
        id=objective_id,
        band=band,
        title=title,
        instruction=instruction,
        prerequisite_ids=requires,
        activity_family_ids=families,
        known_error_pattern_ids=errors,
        hints=hints,
    )


def test_curriculum_bands_cover_the_supported_progression() -> None:
    assert tuple(CurriculumBand) == (
        CurriculumBand.INITIAL,
        CurriculumBand.INTERMEDIATE,
        CurriculumBand.ADVANCED,
    )


@pytest.mark.parametrize("bad_id", ["", " ", " place-value", "place-value "])
def test_hint_rejects_an_id_that_is_not_trimmed_and_nonempty(bad_id: str) -> None:
    with pytest.raises(InvalidCurriculum, match="hint id"):
        hint(bad_id)


@pytest.mark.parametrize("bad_text", ["", " ", " Ayuda", "Ayuda "])
def test_hint_rejects_text_that_is_not_trimmed_and_nonempty(bad_text: str) -> None:
    with pytest.raises(InvalidCurriculum, match="hint text"):
        HintDefinition(id="help", text=bad_text, order=1)


@pytest.mark.parametrize("bad_order", [0, -1, 1.5, "1", True])
def test_hint_rejects_an_order_that_is_not_a_positive_integer(
    bad_order: object,
) -> None:
    with pytest.raises(InvalidCurriculum, match="hint order"):
        HintDefinition(
            id="help",
            text="Cuenta los objetos.",
            order=bad_order,  # type: ignore[arg-type]
        )


def test_hint_is_immutable() -> None:
    definition = hint()

    with pytest.raises(FrozenInstanceError):
        definition.order = 2  # type: ignore[misc]


@pytest.mark.parametrize("bad_id", ["", " ", " place-value", "place-value "])
def test_objective_rejects_an_id_that_is_not_trimmed_and_nonempty(
    bad_id: str,
) -> None:
    with pytest.raises(InvalidCurriculum, match="objective id"):
        objective(bad_id)


def test_objective_rejects_an_unsupported_band() -> None:
    with pytest.raises(InvalidCurriculum, match="band"):
        objective("place-value", band="initial")  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["title", "instruction"])
@pytest.mark.parametrize("bad_text", ["", " ", " Texto", "Texto "])
def test_objective_rejects_wording_that_is_not_trimmed_and_nonempty(
    field: str, bad_text: str
) -> None:
    values = {field: bad_text}

    with pytest.raises(InvalidCurriculum, match=field):
        objective("place-value", **values)  # type: ignore[arg-type]


def test_objective_rejects_empty_activity_families() -> None:
    with pytest.raises(InvalidCurriculum, match="activity family"):
        objective("place-value", families=())


@pytest.mark.parametrize(
    ("field", "values", "message"),
    [
        ("requires", ("counting", "counting"), "prerequisite"),
        ("families", ("oral", "oral"), "activity family"),
        ("errors", ("reversal", "reversal"), "error pattern"),
    ],
)
def test_objective_rejects_duplicate_reference_ids(
    field: str, values: tuple[str, ...], message: str
) -> None:
    arguments = {field: values}

    with pytest.raises(InvalidCurriculum, match=message):
        objective("place-value", **arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("requires", "prerequisite"),
        ("families", "activity family"),
        ("errors", "error pattern"),
    ],
)
def test_objective_rejects_reference_ids_that_are_not_trimmed_and_nonempty(
    field: str, message: str
) -> None:
    arguments = {field: (" invalid",)}

    with pytest.raises(InvalidCurriculum, match=message):
        objective("place-value", **arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("requires", "prerequisite"),
        ("families", "activity family"),
        ("errors", "error pattern"),
    ],
)
def test_objective_rejects_a_scalar_string_for_reference_sequences(
    field: str, message: str
) -> None:
    arguments = {field: "x"}

    with pytest.raises(InvalidCurriculum, match=message):
        objective("place-value", **arguments)  # type: ignore[arg-type]


def test_objective_rejects_an_empty_hint_sequence() -> None:
    with pytest.raises(InvalidCurriculum, match="hint"):
        objective("place-value", hints=())


def test_objective_rejects_duplicate_hint_ids() -> None:
    with pytest.raises(InvalidCurriculum, match="duplicate hint id"):
        objective(
            "place-value",
            hints=(hint("same", 1), hint("same", 2)),
        )


@pytest.mark.parametrize(
    "hints",
    [
        (hint("first", 2),),
        (hint("first", 1), hint("third", 3)),
        (hint("second", 2), hint("first", 1)),
    ],
)
def test_objective_rejects_hint_orders_that_are_not_contiguous_from_one(
    hints: tuple[HintDefinition, ...],
) -> None:
    with pytest.raises(InvalidCurriculum, match="hint order"):
        objective("place-value", hints=hints)


def test_objective_is_immutable() -> None:
    definition = objective("place-value")

    with pytest.raises(FrozenInstanceError):
        definition.title = "Otro título"  # type: ignore[misc]


def test_catalog_rejects_duplicate_objective_ids() -> None:
    with pytest.raises(InvalidCurriculum, match="duplicate objective id"):
        CurriculumCatalog((objective("place-value"), objective("place-value")))


def test_catalog_rejects_a_self_prerequisite() -> None:
    with pytest.raises(InvalidCurriculum, match="itself"):
        CurriculumCatalog(
            (objective("place-value", requires=("place-value",)),)
        )


def test_catalog_rejects_an_unknown_prerequisite() -> None:
    with pytest.raises(InvalidCurriculum, match="unknown prerequisite"):
        CurriculumCatalog(
            (objective("place-value", requires=("missing",)),)
        )


def test_catalog_rejects_a_cycle() -> None:
    with pytest.raises(InvalidCurriculum, match="cycle"):
        CurriculumCatalog(
            (
                objective("units-tens", requires=("compose-2d",)),
                objective("compose-2d", requires=("units-tens",)),
            )
        )


def test_catalog_rejects_a_cycle_in_a_disconnected_component() -> None:
    with pytest.raises(InvalidCurriculum, match="cycle"):
        CurriculumCatalog(
            (
                objective("independent"),
                objective("cycle-a", requires=("cycle-b",)),
                objective("cycle-b", requires=("cycle-a",)),
            )
        )


def test_catalog_topological_order_uses_lexical_ties() -> None:
    catalog = CurriculumCatalog(
        (
            objective("sum", requires=("count",)),
            objective("compare", requires=("count",)),
            objective("zero"),
            objective("count"),
        )
    )

    assert catalog.topological_order == ("count", "compare", "sum", "zero")


def test_catalog_returns_an_objective_by_id() -> None:
    expected = objective("place-value")
    catalog = CurriculumCatalog((expected,))

    assert catalog.objective("place-value") is expected


def test_catalog_reports_an_unknown_objective_clearly() -> None:
    catalog = CurriculumCatalog((objective("place-value"),))

    with pytest.raises(InvalidCurriculum, match="unknown objective 'missing'"):
        catalog.objective("missing")


def test_prerequisites_are_met_when_every_required_objective_is_achieved() -> None:
    catalog = CurriculumCatalog(
        (
            objective("count"),
            objective("place-value", requires=("count",)),
        )
    )

    assert catalog.prerequisites_met("place-value", {"count"}) is True


def test_prerequisites_are_not_met_when_a_required_objective_is_missing() -> None:
    catalog = CurriculumCatalog(
        (
            objective("count"),
            objective("place-value", requires=("count",)),
        )
    )

    assert catalog.prerequisites_met("place-value", set()) is False


def test_two_prerequisites_are_not_met_by_a_partial_achievement_set() -> None:
    catalog = CurriculumCatalog(
        (
            objective("count"),
            objective("compose"),
            objective("addition", requires=("count", "compose")),
        )
    )

    assert catalog.prerequisites_met("addition", {"count"}) is False


def test_two_prerequisites_are_met_by_the_full_achievement_set() -> None:
    catalog = CurriculumCatalog(
        (
            objective("count"),
            objective("compose"),
            objective("addition", requires=("count", "compose")),
        )
    )

    assert catalog.prerequisites_met("addition", {"count", "compose"}) is True


def test_prerequisite_check_rejects_an_unknown_objective() -> None:
    catalog = CurriculumCatalog((objective("place-value"),))

    with pytest.raises(InvalidCurriculum, match="unknown objective 'missing'"):
        catalog.prerequisites_met("missing", set())


def test_catalog_keeps_an_immutable_objective_sequence() -> None:
    source = [objective("place-value")]
    catalog = CurriculumCatalog(source)

    source.append(objective("addition"))

    assert catalog.objectives == (objective("place-value"),)
