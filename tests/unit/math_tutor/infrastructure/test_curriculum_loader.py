"""Boundary tests for strict YAML curriculum loading."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from math_tutor.domain.curriculum import CurriculumCatalog
from math_tutor.domain.templates import ActivityTemplate, ActivityTemplateCatalog
from math_tutor.infrastructure.curriculum_loader import (
    CurriculumLoadError,
    load_curriculum_catalogs,
)


ROOT = Path(__file__).resolve().parents[4]
CURRICULUM_PATH = ROOT / "src/math_tutor/curricula/primary-math-v1.yaml"
TEMPLATES_PATH = ROOT / "src/math_tutor/curricula/activity-templates-v1.yaml"


def _documents() -> tuple[dict[str, object], dict[str, object]]:
    curriculum = yaml.safe_load(CURRICULUM_PATH.read_text(encoding="utf-8"))
    templates = yaml.safe_load(TEMPLATES_PATH.read_text(encoding="utf-8"))
    assert isinstance(curriculum, dict)
    assert isinstance(templates, dict)
    return curriculum, templates


def _write_documents(
    tmp_path: Path,
    curriculum: object,
    templates: object,
) -> tuple[Path, Path]:
    curriculum_path = tmp_path / "curriculum.yaml"
    templates_path = tmp_path / "templates.yaml"
    curriculum_path.write_text(
        yaml.safe_dump(curriculum, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    templates_path.write_text(
        yaml.safe_dump(templates, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return curriculum_path, templates_path


def _load_modified(
    tmp_path: Path,
    curriculum: object,
    templates: object,
) -> tuple[CurriculumCatalog, ActivityTemplateCatalog]:
    paths = _write_documents(tmp_path, curriculum, templates)
    return load_curriculum_catalogs(*paths)


def test_loads_required_vertical_slice_ids_from_explicit_paths() -> None:
    curriculum, templates = load_curriculum_catalogs(
        CURRICULUM_PATH, TEMPLATES_PATH
    )

    assert {
        "units-tens",
        "compose-two-digit",
        "add-within-20",
        "subtract-within-20",
    } <= {objective.id for objective in curriculum.objectives}
    assert templates.templates

def test_loader_returns_typed_catalogs_without_raw_yaml_dictionaries() -> None:
    curriculum, templates = load_curriculum_catalogs(
        CURRICULUM_PATH, TEMPLATES_PATH
    )

    assert isinstance(curriculum, CurriculumCatalog)
    assert isinstance(templates, ActivityTemplateCatalog)
    assert all(isinstance(item, ActivityTemplate) for item in templates.templates)


@pytest.mark.parametrize(
    ("document_name", "version"),
    [("curriculum", "primary-math/v2"), ("templates", "activity-templates/v2")],
)
def test_rejects_an_unknown_schema_version(
    tmp_path: Path, document_name: str, version: str
) -> None:
    curriculum, templates = _documents()
    document = curriculum if document_name == "curriculum" else templates
    document["schema_version"] = version

    with pytest.raises(CurriculumLoadError, match="schema_version"):
        _load_modified(tmp_path, curriculum, templates)


def test_rejects_an_unknown_curriculum_root_key(tmp_path: Path) -> None:
    curriculum, templates = _documents()
    curriculum["unexpected"] = True

    with pytest.raises(CurriculumLoadError, match="unexpected keys.*unexpected"):
        _load_modified(tmp_path, curriculum, templates)


def test_rejects_an_unknown_objective_key(tmp_path: Path) -> None:
    curriculum, templates = _documents()
    curriculum["objectives"][0]["unexpected"] = True  # type: ignore[index]

    with pytest.raises(CurriculumLoadError, match="unexpected keys.*unexpected"):
        _load_modified(tmp_path, curriculum, templates)


def test_rejects_an_unknown_hint_key(tmp_path: Path) -> None:
    curriculum, templates = _documents()
    curriculum["objectives"][0]["hints"][0]["unexpected"] = True  # type: ignore[index]

    with pytest.raises(CurriculumLoadError, match="unexpected keys.*unexpected"):
        _load_modified(tmp_path, curriculum, templates)


def test_rejects_an_unknown_template_key(tmp_path: Path) -> None:
    curriculum, templates = _documents()
    templates["templates"][0]["unexpected"] = True  # type: ignore[index]

    with pytest.raises(CurriculumLoadError, match="unexpected keys.*unexpected"):
        _load_modified(tmp_path, curriculum, templates)


@pytest.mark.parametrize("nested_name", ["parameters", "difficulty", "expected_answer"])
def test_rejects_unknown_keys_in_nested_template_objects(
    tmp_path: Path, nested_name: str
) -> None:
    curriculum, templates = _documents()
    first = templates["templates"][0]  # type: ignore[index]
    nested = first[nested_name]
    if nested_name == "parameters":
        nested = next(iter(nested.values()))
    nested["unexpected"] = True

    with pytest.raises(CurriculumLoadError, match="unexpected keys.*unexpected"):
        _load_modified(tmp_path, curriculum, templates)


def test_rejects_a_missing_required_key(tmp_path: Path) -> None:
    curriculum, templates = _documents()
    del curriculum["objectives"][0]["title_es"]  # type: ignore[index]

    with pytest.raises(CurriculumLoadError, match="missing keys.*title_es"):
        _load_modified(tmp_path, curriculum, templates)


def test_rejects_a_non_mapping_yaml_root(tmp_path: Path) -> None:
    _, templates = _documents()

    with pytest.raises(CurriculumLoadError, match="curriculum.*mapping"):
        _load_modified(tmp_path, [], templates)


def test_rejects_a_scalar_where_an_id_list_is_required(tmp_path: Path) -> None:
    curriculum, templates = _documents()
    curriculum["objectives"][0]["prerequisite_ids"] = "count-to-20"  # type: ignore[index]

    with pytest.raises(CurriculumLoadError, match="prerequisite_ids.*list"):
        _load_modified(tmp_path, curriculum, templates)


@pytest.mark.parametrize(
    ("location", "value"),
    [("parameter", True), ("difficulty", False), ("hint-order", 1.5)],
)
def test_rejects_wrong_numeric_types_and_booleans(
    tmp_path: Path, location: str, value: object
) -> None:
    curriculum, templates = _documents()
    if location == "parameter":
        first_parameter = next(
            iter(templates["templates"][0]["parameters"].values())  # type: ignore[index]
        )
        first_parameter["min"] = value
    elif location == "difficulty":
        templates["templates"][0]["difficulty"]["max"] = value  # type: ignore[index]
    else:
        curriculum["objectives"][0]["hints"][0]["order"] = value  # type: ignore[index]

    with pytest.raises(CurriculumLoadError, match="integer"):
        _load_modified(tmp_path, curriculum, templates)


def test_rejects_an_unknown_activity_family(tmp_path: Path) -> None:
    curriculum, templates = _documents()
    curriculum["objectives"][0]["activity_families"][0] = "multiplication"  # type: ignore[index]

    with pytest.raises(CurriculumLoadError, match="activity family"):
        _load_modified(tmp_path, curriculum, templates)


def test_rejects_duplicate_objective_ids(tmp_path: Path) -> None:
    curriculum, templates = _documents()
    curriculum["objectives"].append(curriculum["objectives"][0])  # type: ignore[union-attr,index]

    with pytest.raises(CurriculumLoadError, match="duplicate objective"):
        _load_modified(tmp_path, curriculum, templates)


def test_rejects_duplicate_template_ids(tmp_path: Path) -> None:
    curriculum, templates = _documents()
    templates["templates"].append(templates["templates"][0])  # type: ignore[union-attr,index]

    with pytest.raises(CurriculumLoadError, match="duplicate template"):
        _load_modified(tmp_path, curriculum, templates)


@pytest.mark.parametrize("wording", ["title_es", "instruction_es"])
def test_rejects_missing_spanish_objective_wording(
    tmp_path: Path, wording: str
) -> None:
    curriculum, templates = _documents()
    curriculum["objectives"][0][wording] = ""  # type: ignore[index]

    with pytest.raises(CurriculumLoadError, match=wording.removesuffix("_es")):
        _load_modified(tmp_path, curriculum, templates)


def test_rejects_missing_spanish_template_wording(tmp_path: Path) -> None:
    curriculum, templates = _documents()
    templates["templates"][0]["prompt_es"] = ""  # type: ignore[index]

    with pytest.raises(CurriculumLoadError, match="prompt"):
        _load_modified(tmp_path, curriculum, templates)


def test_rejects_an_invalid_prerequisite_reference(tmp_path: Path) -> None:
    curriculum, templates = _documents()
    curriculum["objectives"][1]["prerequisite_ids"] = ["missing"]  # type: ignore[index]

    with pytest.raises(CurriculumLoadError, match="unknown prerequisite"):
        _load_modified(tmp_path, curriculum, templates)


@pytest.mark.parametrize(
    ("reference", "bad_value", "message"),
    [
        ("objective_id", "missing", "unknown objective"),
        ("family", "subtraction", "family.*not allowed"),
        ("error_pattern_ids", ["missing"], "unknown error pattern"),
        ("hint_ids", ["missing"], "unknown hint"),
    ],
)
def test_rejects_invalid_template_references(
    tmp_path: Path, reference: str, bad_value: object, message: str
) -> None:
    curriculum, templates = _documents()
    templates["templates"][0][reference] = bad_value  # type: ignore[index]

    with pytest.raises(CurriculumLoadError, match=message):
        _load_modified(tmp_path, curriculum, templates)


def test_rejects_a_catalog_below_the_reviewed_template_gate(tmp_path: Path) -> None:
    curriculum, templates = _documents()
    templates["templates"] = templates["templates"][:29]  # type: ignore[index]

    with pytest.raises(CurriculumLoadError, match="30.*50"):
        _load_modified(tmp_path, curriculum, templates)


def test_rejects_an_objective_without_template_coverage(tmp_path: Path) -> None:
    curriculum, templates = _documents()
    uncovered = curriculum["objectives"][-1]["id"]  # type: ignore[index]
    templates["templates"] = [  # type: ignore[index]
        item
        for item in templates["templates"]  # type: ignore[union-attr]
        if item["objective_id"] != uncovered
    ]
    while len(templates["templates"]) < 30:  # type: ignore[arg-type,index]
        clone = dict(templates["templates"][0])  # type: ignore[index]
        clone["id"] = f"coverage-filler-{len(templates['templates'])}"  # type: ignore[arg-type]
        templates["templates"].append(clone)  # type: ignore[union-attr,index]

    with pytest.raises(CurriculumLoadError, match="without activity templates"):
        _load_modified(tmp_path, curriculum, templates)


def test_wraps_malformed_yaml_with_a_clear_loader_error(tmp_path: Path) -> None:
    _, templates = _documents()
    curriculum_path, templates_path = _write_documents(tmp_path, {}, templates)
    curriculum_path.write_text("objectives: [", encoding="utf-8")

    with pytest.raises(CurriculumLoadError, match="could not parse"):
        load_curriculum_catalogs(curriculum_path, templates_path)


def test_explicit_paths_do_not_depend_on_the_current_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    curriculum, templates = load_curriculum_catalogs(
        CURRICULUM_PATH, TEMPLATES_PATH
    )

    assert curriculum.objectives
    assert templates.templates
