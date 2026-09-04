"""Immutable, vendor-free models for reviewed activity templates."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from re import fullmatch
from string import Formatter
from types import MappingProxyType
from typing import Any


class InvalidActivityTemplate(ValueError):
    """Raised when a reviewed activity template violates its contract."""


class ActivityFamily(Enum):
    """Supported activity families for the first primary-maths slice."""

    COUNTING = "counting"
    NUMBER_SEQUENCE = "number-sequence"
    COMPARISON = "comparison"
    PLACE_VALUE = "place-value"
    COMPOSITION = "composition"
    DECOMPOSITION = "decomposition"
    NUMBER_LINE = "number-line"
    ADDITION = "addition"
    SUBTRACTION = "subtraction"


class ExpectedAnswerKind(Enum):
    """Shape of the structured answer expected by deterministic checking."""

    INTEGER = "integer"
    INTEGER_PAIR = "integer-pair"
    RELATION = "relation"
    INTEGER_SEQUENCE = "integer-sequence"


class AnswerConstraint(Enum):
    """Reviewed constraints available to the later deterministic generator."""

    NON_NEGATIVE = "non-negative"
    WITHIN_10 = "within-10"
    WITHIN_20 = "within-20"
    TWO_DIGIT = "two-digit"
    NO_CARRY = "no-carry"
    NO_BORROW = "no-borrow"
    ASCENDING = "ascending"
    DESCENDING = "descending"
    ADJACENT = "adjacent"
    DISTINCT = "distinct"


def _require_trimmed(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise InvalidActivityTemplate(
            f"{label} must be a trimmed, nonempty string"
        )
    return value


def _require_stable_id(value: object, *, label: str) -> str:
    identifier = _require_trimmed(value, label=label)
    if fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", identifier) is None:
        raise InvalidActivityTemplate(
            f"{label} must be a stable lowercase hyphenated identifier"
        )
    return identifier


def _tuple_from_iterable(value: object, *, label: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise InvalidActivityTemplate(f"{label} must be a non-scalar sequence")
    return tuple(value)


def _validated_ids(value: object, *, label: str) -> tuple[str, ...]:
    result = _tuple_from_iterable(value, label=label)
    if not result:
        raise InvalidActivityTemplate(f"{label} ids must not be empty")
    for item in result:
        _require_trimmed(item, label=f"{label} id")
    if len(set(result)) != len(result):
        raise InvalidActivityTemplate(f"duplicate {label} id")
    return result


@dataclass(frozen=True, slots=True)
class ParameterBounds:
    """Inclusive integer bounds for one generated template parameter."""

    minimum: int
    maximum: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.minimum, bool)
            or not isinstance(self.minimum, int)
            or isinstance(self.maximum, bool)
            or not isinstance(self.maximum, int)
        ):
            raise InvalidActivityTemplate(
                "parameter bounds must use integer minimum and maximum values"
            )
        if self.minimum > self.maximum:
            raise InvalidActivityTemplate(
                "parameter minimum must not exceed maximum"
            )


@dataclass(frozen=True, slots=True)
class ExpectedAnswerSpec:
    """Typed shape and constraints for a deterministic expected answer."""

    kind: ExpectedAnswerKind
    fields: tuple[str, ...]
    constraints: tuple[AnswerConstraint, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ExpectedAnswerKind):
            raise InvalidActivityTemplate(
                "expected answer kind must be an ExpectedAnswerKind"
            )

        fields = _tuple_from_iterable(self.fields, label="fields")
        if not fields:
            raise InvalidActivityTemplate("expected answer fields must not be empty")
        for name in fields:
            _require_trimmed(name, label="expected answer field")
        if len(set(fields)) != len(fields):
            raise InvalidActivityTemplate(
                "expected answer fields must be unique"
            )

        constraints = _tuple_from_iterable(
            self.constraints, label="constraints"
        )
        if not all(isinstance(item, AnswerConstraint) for item in constraints):
            raise InvalidActivityTemplate(
                "expected answer constraints must be AnswerConstraint values"
            )
        if len(set(constraints)) != len(constraints):
            raise InvalidActivityTemplate(
                "expected answer constraints must be unique"
            )

        object.__setattr__(self, "fields", fields)
        object.__setattr__(self, "constraints", constraints)


@dataclass(frozen=True, slots=True)
class ActivityTemplate:
    """One reviewed Spanish, parameterised activity definition."""

    id: str
    objective_id: str
    family: ActivityFamily
    parameter_bounds: Mapping[str, ParameterBounds]
    difficulty_min: int
    difficulty_max: int
    prompt_template_es: str
    expected_answer: ExpectedAnswerSpec
    error_pattern_ids: tuple[str, ...]
    hint_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_stable_id(self.id, label="id")
        _require_stable_id(self.objective_id, label="objective id")
        _require_trimmed(self.prompt_template_es, label="prompt")
        if not isinstance(self.family, ActivityFamily):
            raise InvalidActivityTemplate(
                "template family must be an ActivityFamily"
            )
        if not isinstance(self.parameter_bounds, Mapping):
            raise InvalidActivityTemplate(
                "parameter bounds must be a mapping of ParameterBounds values"
            )

        parameters: dict[str, ParameterBounds] = {}
        for name, bounds in self.parameter_bounds.items():
            _require_trimmed(name, label="parameter name")
            if not isinstance(bounds, ParameterBounds):
                raise InvalidActivityTemplate(
                    "parameter values must be ParameterBounds instances"
                )
            parameters[name] = bounds
        if not parameters:
            raise InvalidActivityTemplate(
                "a reviewed template must define at least one parameter"
            )

        try:
            placeholders = {
                field_name
                for _, field_name, _, _ in Formatter().parse(
                    self.prompt_template_es
                )
                if field_name is not None
            }
        except ValueError as error:
            raise InvalidActivityTemplate(
                "prompt placeholders must use valid format syntax"
            ) from error
        if placeholders != set(parameters):
            raise InvalidActivityTemplate(
                "prompt placeholder names must exactly match parameter names"
            )

        if (
            isinstance(self.difficulty_min, bool)
            or not isinstance(self.difficulty_min, int)
            or isinstance(self.difficulty_max, bool)
            or not isinstance(self.difficulty_max, int)
            or not 1 <= self.difficulty_min <= self.difficulty_max <= 5
        ):
            raise InvalidActivityTemplate(
                "difficulty bounds must be ordered integers between 1 and 5"
            )
        if not isinstance(self.expected_answer, ExpectedAnswerSpec):
            raise InvalidActivityTemplate(
                "expected answer must be an ExpectedAnswerSpec"
            )

        errors = _validated_ids(self.error_pattern_ids, label="error pattern")
        hints = _validated_ids(self.hint_ids, label="hint")

        object.__setattr__(
            self,
            "parameter_bounds",
            MappingProxyType(dict(sorted(parameters.items()))),
        )
        object.__setattr__(self, "error_pattern_ids", errors)
        object.__setattr__(self, "hint_ids", hints)


@dataclass(frozen=True, slots=True)
class ActivityTemplateCatalog:
    """Immutable template catalog with stable deterministic indexes."""

    templates: tuple[ActivityTemplate, ...]
    _templates_by_id: Mapping[str, ActivityTemplate] = field(
        init=False, repr=False, compare=False
    )
    _templates_by_objective: Mapping[str, tuple[ActivityTemplate, ...]] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if isinstance(self.templates, (str, bytes)) or not isinstance(
            self.templates, Iterable
        ):
            raise InvalidActivityTemplate(
                "catalog entries must be ActivityTemplate values"
            )
        templates = tuple(self.templates)
        if not templates or not all(
            isinstance(item, ActivityTemplate) for item in templates
        ):
            raise InvalidActivityTemplate(
                "catalog entries must be ActivityTemplate values"
            )

        ordered = tuple(sorted(templates, key=lambda item: item.id))
        by_id: dict[str, ActivityTemplate] = {}
        by_objective: dict[str, list[ActivityTemplate]] = {}
        for definition in ordered:
            if definition.id in by_id:
                raise InvalidActivityTemplate(
                    f"duplicate template id '{definition.id}'"
                )
            by_id[definition.id] = definition
            by_objective.setdefault(definition.objective_id, []).append(
                definition
            )

        object.__setattr__(self, "templates", ordered)
        object.__setattr__(self, "_templates_by_id", MappingProxyType(by_id))
        object.__setattr__(
            self,
            "_templates_by_objective",
            MappingProxyType(
                {key: tuple(value) for key, value in by_objective.items()}
            ),
        )

    def template(self, template_id: str) -> ActivityTemplate:
        """Return one template by its stable identifier."""

        try:
            return self._templates_by_id[template_id]
        except KeyError as error:
            raise InvalidActivityTemplate(
                f"unknown template '{template_id}'"
            ) from error

    def for_objective(self, objective_id: str) -> tuple[ActivityTemplate, ...]:
        """Return templates for an objective in lexical template-id order."""

        return self._templates_by_objective.get(objective_id, ())
