"""Strict YAML boundary for reviewed curriculum and activity catalogs."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from math_tutor.domain.curriculum import (
    CurriculumBand,
    CurriculumCatalog,
    HintDefinition,
    InvalidCurriculum,
    LearningObjective,
)
from math_tutor.domain.templates import (
    ActivityFamily,
    ActivityTemplate,
    ActivityTemplateCatalog,
    AnswerDerivation,
    AnswerDerivationOperation,
    AnswerConstraint,
    ExpectedAnswerKind,
    ExpectedAnswerSpec,
    InvalidActivityTemplate,
    ParameterBounds,
    ParameterRelation,
    ParameterRelationKind,
)


CURRICULUM_SCHEMA_VERSION = "primary-math/v1"
TEMPLATE_SCHEMA_VERSION = "activity-templates/v1"


class CurriculumLoadError(ValueError):
    """Raised when reviewed YAML cannot form valid typed catalogs."""


def _read_yaml(path: Path, *, label: str) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise CurriculumLoadError(
            f"could not read {label} YAML at '{path}': {error}"
        ) from error
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise CurriculumLoadError(
            f"could not parse {label} YAML at '{path}': {error}"
        ) from error


def _exact_mapping(
    value: object, *, keys: set[str], context: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CurriculumLoadError(f"{context} must be a mapping")
    if not all(isinstance(key, str) for key in value):
        raise CurriculumLoadError(f"{context} keys must be strings")
    actual = set(value)
    unexpected = sorted(actual - keys)
    if unexpected:
        raise CurriculumLoadError(
            f"{context} has unexpected keys: {', '.join(map(str, unexpected))}"
        )
    missing = sorted(keys - actual)
    if missing:
        raise CurriculumLoadError(
            f"{context} has missing keys: {', '.join(missing)}"
        )
    return value  # type: ignore[return-value]


def _list(value: object, *, context: str) -> list[object]:
    if not isinstance(value, list):
        raise CurriculumLoadError(f"{context} must be a list")
    return value


def _string(value: object, *, context: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CurriculumLoadError(
            f"{context} must be a trimmed, nonempty string"
        )
    return value


def _integer(value: object, *, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CurriculumLoadError(f"{context} must be an integer")
    return value


def _string_list(value: object, *, context: str) -> tuple[str, ...]:
    values = _list(value, context=context)
    return tuple(
        _string(item, context=f"{context}[{index}]")
        for index, item in enumerate(values)
    )


def _enum_member(enum_type: type[Any], value: object, *, context: str) -> Any:
    string_value = _string(value, context=context)
    try:
        return enum_type(string_value)
    except ValueError as error:
        raise CurriculumLoadError(
            f"{context} has unknown value '{string_value}'"
        ) from error


def _parse_hint(value: object, *, context: str) -> HintDefinition:
    item = _exact_mapping(
        value,
        keys={"id", "text_es", "order"},
        context=context,
    )
    return HintDefinition(
        id=_string(item["id"], context=f"{context}.id"),
        text=_string(item["text_es"], context=f"{context}.text_es"),
        order=_integer(item["order"], context=f"{context}.order integer"),
    )


def _parse_objective(value: object, *, context: str) -> LearningObjective:
    item = _exact_mapping(
        value,
        keys={
            "id",
            "band",
            "title_es",
            "instruction_es",
            "prerequisite_ids",
            "activity_families",
            "known_error_pattern_ids",
            "hints",
        },
        context=context,
    )
    family_ids = _string_list(
        item["activity_families"], context=f"{context}.activity_families"
    )
    known_families = {family.value for family in ActivityFamily}
    unknown_families = sorted(set(family_ids) - known_families)
    if unknown_families:
        raise CurriculumLoadError(
            f"{context} has unknown activity family '{unknown_families[0]}'"
        )

    hints = tuple(
        _parse_hint(hint, context=f"{context}.hints[{index}]")
        for index, hint in enumerate(
            _list(item["hints"], context=f"{context}.hints")
        )
    )
    return LearningObjective(
        id=_string(item["id"], context=f"{context}.id"),
        band=_enum_member(
            CurriculumBand, item["band"], context=f"{context}.band"
        ),
        title=_string(item["title_es"], context=f"{context}.title"),
        instruction=_string(
            item["instruction_es"], context=f"{context}.instruction"
        ),
        prerequisite_ids=_string_list(
            item["prerequisite_ids"],
            context=f"{context}.prerequisite_ids",
        ),
        activity_family_ids=family_ids,
        known_error_pattern_ids=_string_list(
            item["known_error_pattern_ids"],
            context=f"{context}.known_error_pattern_ids",
        ),
        hints=hints,
    )


def _parse_curriculum(document: object) -> CurriculumCatalog:
    root = _exact_mapping(
        document,
        keys={"schema_version", "objectives"},
        context="curriculum root",
    )
    version = _string(root["schema_version"], context="schema_version")
    if version != CURRICULUM_SCHEMA_VERSION:
        raise CurriculumLoadError(
            f"unsupported curriculum schema_version '{version}'"
        )
    values = _list(root["objectives"], context="curriculum objectives")
    if not 8 <= len(values) <= 12:
        raise CurriculumLoadError(
            "curriculum must contain between 8 and 12 reviewed objectives"
        )
    objectives = tuple(
        _parse_objective(value, context=f"objectives[{index}]")
        for index, value in enumerate(values)
    )
    return CurriculumCatalog(objectives)


def _parse_parameters(
    value: object, *, context: str
) -> Mapping[str, ParameterBounds]:
    if not isinstance(value, Mapping):
        raise CurriculumLoadError(f"{context} must be a mapping")
    if not value:
        raise CurriculumLoadError(f"{context} must not be empty")
    parameters: dict[str, ParameterBounds] = {}
    for raw_name, raw_bounds in value.items():
        name = _string(raw_name, context=f"{context} parameter name")
        bounds = _exact_mapping(
            raw_bounds,
            keys={"min", "max"},
            context=f"{context}.{name}",
        )
        parameters[name] = ParameterBounds(
            minimum=_integer(
                bounds["min"], context=f"{context}.{name}.min integer"
            ),
            maximum=_integer(
                bounds["max"], context=f"{context}.{name}.max integer"
            ),
        )
    return parameters


def _parse_expected_answer(
    value: object, *, context: str
) -> ExpectedAnswerSpec:
    item = _exact_mapping(
        value,
        keys={"kind", "fields", "constraints", "derivations"},
        context=context,
    )
    constraints = tuple(
        _enum_member(
            AnswerConstraint,
            constraint,
            context=f"{context}.constraints[{index}]",
        )
        for index, constraint in enumerate(
            _list(item["constraints"], context=f"{context}.constraints")
        )
    )
    derivations = tuple(
        _parse_answer_derivation(
            derivation, context=f"{context}.derivations[{index}]"
        )
        for index, derivation in enumerate(
            _list(item["derivations"], context=f"{context}.derivations")
        )
    )
    return ExpectedAnswerSpec(
        kind=_enum_member(
            ExpectedAnswerKind, item["kind"], context=f"{context}.kind"
        ),
        fields=_string_list(item["fields"], context=f"{context}.fields"),
        constraints=constraints,
        derivations=derivations,
    )


def _parse_answer_derivation(
    value: object, *, context: str
) -> AnswerDerivation:
    item = _exact_mapping(
        value,
        keys={"field", "operation", "parameters"},
        context=context,
    )
    return AnswerDerivation(
        field=_string(item["field"], context=f"{context}.field"),
        operation=_enum_member(
            AnswerDerivationOperation,
            item["operation"],
            context=f"{context}.operation",
        ),
        parameters=_string_list(
            item["parameters"], context=f"{context}.parameters"
        ),
    )


def _parse_parameter_relation(
    value: object, *, context: str
) -> ParameterRelation:
    item = _exact_mapping(
        value,
        keys={"kind", "parameters", "value"},
        context=context,
    )
    raw_relation_value = item["value"]
    relation_value = (
        None
        if raw_relation_value is None
        else _integer(raw_relation_value, context=f"{context}.value integer")
    )
    return ParameterRelation(
        kind=_enum_member(
            ParameterRelationKind,
            item["kind"],
            context=f"{context}.kind",
        ),
        parameters=_string_list(
            item["parameters"], context=f"{context}.parameters"
        ),
        value=relation_value,
    )


def _parse_template(value: object, *, context: str) -> ActivityTemplate:
    item = _exact_mapping(
        value,
        keys={
            "id",
            "objective_id",
            "family",
            "parameters",
            "difficulty",
            "prompt_es",
            "expected_answer",
            "parameter_relations",
            "error_pattern_ids",
            "hint_ids",
        },
        context=context,
    )
    difficulty = _exact_mapping(
        item["difficulty"],
        keys={"min", "max"},
        context=f"{context}.difficulty",
    )
    return ActivityTemplate(
        id=_string(item["id"], context=f"{context}.id"),
        objective_id=_string(
            item["objective_id"], context=f"{context}.objective_id"
        ),
        family=_enum_member(
            ActivityFamily, item["family"], context=f"{context}.family"
        ),
        parameter_bounds=_parse_parameters(
            item["parameters"], context=f"{context}.parameters"
        ),
        difficulty_min=_integer(
            difficulty["min"], context=f"{context}.difficulty.min integer"
        ),
        difficulty_max=_integer(
            difficulty["max"], context=f"{context}.difficulty.max integer"
        ),
        prompt_template_es=_string(
            item["prompt_es"], context=f"{context}.prompt"
        ),
        expected_answer=_parse_expected_answer(
            item["expected_answer"], context=f"{context}.expected_answer"
        ),
        parameter_relations=tuple(
            _parse_parameter_relation(
                relation,
                context=f"{context}.parameter_relations[{index}]",
            )
            for index, relation in enumerate(
                _list(
                    item["parameter_relations"],
                    context=f"{context}.parameter_relations",
                )
            )
        ),
        error_pattern_ids=_string_list(
            item["error_pattern_ids"],
            context=f"{context}.error_pattern_ids",
        ),
        hint_ids=_string_list(item["hint_ids"], context=f"{context}.hint_ids"),
    )


def _parse_templates(document: object) -> ActivityTemplateCatalog:
    root = _exact_mapping(
        document,
        keys={"schema_version", "templates"},
        context="activity-template root",
    )
    version = _string(root["schema_version"], context="schema_version")
    if version != TEMPLATE_SCHEMA_VERSION:
        raise CurriculumLoadError(
            f"unsupported activity-template schema_version '{version}'"
        )
    values = _list(root["templates"], context="activity templates")
    if not 30 <= len(values) <= 50:
        raise CurriculumLoadError(
            "activity catalog must contain between 30 and 50 reviewed templates"
        )
    templates = tuple(
        _parse_template(value, context=f"templates[{index}]")
        for index, value in enumerate(values)
    )
    return ActivityTemplateCatalog(templates)


def _validate_references(
    curriculum: CurriculumCatalog,
    templates: ActivityTemplateCatalog,
) -> None:
    objective_ids = {objective.id for objective in curriculum.objectives}
    covered_ids: set[str] = set()
    for definition in templates.templates:
        if definition.objective_id not in objective_ids:
            raise CurriculumLoadError(
                f"template '{definition.id}' references unknown objective "
                f"'{definition.objective_id}'"
            )
        objective = curriculum.objective(definition.objective_id)
        covered_ids.add(objective.id)
        if definition.family.value not in objective.activity_family_ids:
            raise CurriculumLoadError(
                f"template '{definition.id}' family '{definition.family.value}' "
                f"is not allowed by objective '{objective.id}'"
            )

        unknown_errors = sorted(
            set(definition.error_pattern_ids)
            - set(objective.known_error_pattern_ids)
        )
        if unknown_errors:
            raise CurriculumLoadError(
                f"template '{definition.id}' references unknown error pattern "
                f"'{unknown_errors[0]}' for objective '{objective.id}'"
            )
        objective_hint_ids = {hint.id for hint in objective.hints}
        unknown_hints = sorted(set(definition.hint_ids) - objective_hint_ids)
        if unknown_hints:
            raise CurriculumLoadError(
                f"template '{definition.id}' references unknown hint "
                f"'{unknown_hints[0]}' for objective '{objective.id}'"
            )

    uncovered = sorted(objective_ids - covered_ids)
    if uncovered:
        raise CurriculumLoadError(
            "objectives without activity templates: " + ", ".join(uncovered)
        )


def load_curriculum_catalogs(
    curriculum_path: str | Path,
    templates_path: str | Path,
) -> tuple[CurriculumCatalog, ActivityTemplateCatalog]:
    """Load validated catalogs from two explicit filesystem paths."""

    curriculum_file = Path(curriculum_path)
    templates_file = Path(templates_path)
    try:
        curriculum = _parse_curriculum(
            _read_yaml(curriculum_file, label="curriculum")
        )
        templates = _parse_templates(
            _read_yaml(templates_file, label="activity-template")
        )
        _validate_references(curriculum, templates)
    except CurriculumLoadError:
        raise
    except (InvalidCurriculum, InvalidActivityTemplate) as error:
        raise CurriculumLoadError(str(error)) from error
    return curriculum, templates
