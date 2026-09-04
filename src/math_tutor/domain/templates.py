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


class AnswerDerivationOperation(Enum):
    """Typed operations from reviewed parameters to one answer field."""

    VALUE = "value"
    SUM = "sum"
    DIFFERENCE = "difference"
    DOUBLE = "double"
    MINIMUM = "minimum"
    MAXIMUM = "maximum"
    RELATION = "relation"
    SUCCESSOR = "successor"
    PREDECESSOR = "predecessor"
    FOLLOWING_SEQUENCE = "following-sequence"
    TENS_DIGIT = "tens-digit"
    UNITS_DIGIT = "units-digit"
    TENS_VALUE = "tens-value"
    COMPOSE_TENS_UNITS = "compose-tens-units"


class ParameterRelationKind(Enum):
    """Supported coherence rules between reviewed template parameters."""

    DISTINCT = "distinct"
    EQUALS_TENS_DIGIT = "equals-tens-digit"
    OFFSET_EQUALS = "offset-equals"
    MULTIPLE_OF = "multiple-of"


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
class AnswerDerivation:
    """Declarative formula for deriving one structured answer field."""

    field: str
    operation: AnswerDerivationOperation
    parameters: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_trimmed(self.field, label="derivation field")
        if not isinstance(self.operation, AnswerDerivationOperation):
            raise InvalidActivityTemplate(
                "derivation operation must be an AnswerDerivationOperation"
            )
        parameters = _tuple_from_iterable(
            self.parameters, label="derivation parameters"
        )
        for name in parameters:
            _require_trimmed(name, label="derivation parameter")

        one_parameter = {
            AnswerDerivationOperation.VALUE,
            AnswerDerivationOperation.DOUBLE,
            AnswerDerivationOperation.SUCCESSOR,
            AnswerDerivationOperation.PREDECESSOR,
            AnswerDerivationOperation.TENS_DIGIT,
            AnswerDerivationOperation.UNITS_DIGIT,
            AnswerDerivationOperation.TENS_VALUE,
        }
        two_parameters = {
            AnswerDerivationOperation.DIFFERENCE,
            AnswerDerivationOperation.MINIMUM,
            AnswerDerivationOperation.MAXIMUM,
            AnswerDerivationOperation.RELATION,
            AnswerDerivationOperation.FOLLOWING_SEQUENCE,
            AnswerDerivationOperation.COMPOSE_TENS_UNITS,
        }
        if self.operation in one_parameter and len(parameters) != 1:
            raise InvalidActivityTemplate(
                f"derivation operation '{self.operation.value}' requires one parameter"
            )
        if self.operation in two_parameters and len(parameters) != 2:
            raise InvalidActivityTemplate(
                f"derivation operation '{self.operation.value}' requires two parameters"
            )
        if self.operation is AnswerDerivationOperation.SUM and len(parameters) < 2:
            raise InvalidActivityTemplate(
                "derivation operation 'sum' requires at least two parameters"
            )
        object.__setattr__(self, "parameters", parameters)


@dataclass(frozen=True, slots=True)
class ParameterRelation:
    """Declarative rule limiting coherent parameter combinations."""

    kind: ParameterRelationKind
    parameters: tuple[str, ...]
    value: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ParameterRelationKind):
            raise InvalidActivityTemplate(
                "parameter relation kind must be a ParameterRelationKind"
            )
        parameters = _tuple_from_iterable(
            self.parameters, label="relation parameters"
        )
        for name in parameters:
            _require_trimmed(name, label="relation parameter")

        expected_arity = {
            ParameterRelationKind.DISTINCT: 2,
            ParameterRelationKind.EQUALS_TENS_DIGIT: 2,
            ParameterRelationKind.OFFSET_EQUALS: 2,
            ParameterRelationKind.MULTIPLE_OF: 1,
        }[self.kind]
        if len(parameters) != expected_arity:
            raise InvalidActivityTemplate(
                f"parameter relation '{self.kind.value}' requires "
                f"{expected_arity} parameter(s)"
            )
        if len(set(parameters)) != len(parameters):
            raise InvalidActivityTemplate(
                "parameter relation parameters must be distinct"
            )

        requires_value = self.kind in {
            ParameterRelationKind.OFFSET_EQUALS,
            ParameterRelationKind.MULTIPLE_OF,
        }
        if requires_value:
            if isinstance(self.value, bool) or not isinstance(self.value, int):
                raise InvalidActivityTemplate(
                    f"parameter relation '{self.kind.value}' requires an integer value"
                )
            if self.kind is ParameterRelationKind.MULTIPLE_OF and self.value <= 0:
                raise InvalidActivityTemplate(
                    "parameter relation 'multiple-of' requires a positive value"
                )
        elif self.value is not None:
            raise InvalidActivityTemplate(
                f"parameter relation '{self.kind.value}' does not accept a value"
            )
        object.__setattr__(self, "parameters", parameters)

    def is_satisfied(self, values: Mapping[str, int]) -> bool:
        """Return whether already type-checked parameter values satisfy the rule."""

        operands = tuple(values[name] for name in self.parameters)
        if self.kind is ParameterRelationKind.DISTINCT:
            return operands[0] != operands[1]
        if self.kind is ParameterRelationKind.EQUALS_TENS_DIGIT:
            return operands[0] == operands[1] // 10
        if self.kind is ParameterRelationKind.OFFSET_EQUALS:
            assert self.value is not None
            return operands[0] == operands[1] + self.value
        assert self.kind is ParameterRelationKind.MULTIPLE_OF
        assert self.value is not None
        return operands[0] % self.value == 0


@dataclass(frozen=True, slots=True)
class ExpectedAnswerSpec:
    """Typed shape and constraints for a deterministic expected answer."""

    kind: ExpectedAnswerKind
    fields: tuple[str, ...]
    constraints: tuple[AnswerConstraint, ...]
    derivations: tuple[AnswerDerivation, ...]

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

        derivations = _tuple_from_iterable(
            self.derivations, label="derivations"
        )
        if not all(isinstance(item, AnswerDerivation) for item in derivations):
            raise InvalidActivityTemplate(
                "expected answer derivations must be AnswerDerivation values"
            )
        derivation_fields = tuple(item.field for item in derivations)
        if len(set(derivation_fields)) != len(derivation_fields):
            raise InvalidActivityTemplate(
                "expected answer derivation fields must be unique"
            )
        if set(derivation_fields) != set(fields):
            raise InvalidActivityTemplate(
                "expected answer derivation fields must exactly match answer fields"
            )

        object.__setattr__(self, "fields", fields)
        object.__setattr__(self, "constraints", constraints)
        object.__setattr__(self, "derivations", derivations)


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
    parameter_relations: tuple[ParameterRelation, ...]
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

        derivation_parameters = {
            name
            for derivation in self.expected_answer.derivations
            for name in derivation.parameters
        }
        unknown_derivation_parameters = sorted(
            derivation_parameters - set(parameters)
        )
        if unknown_derivation_parameters:
            raise InvalidActivityTemplate(
                "expected answer derivation references unknown parameter "
                f"'{unknown_derivation_parameters[0]}'"
            )

        relations = _tuple_from_iterable(
            self.parameter_relations, label="parameter relations"
        )
        if not all(isinstance(item, ParameterRelation) for item in relations):
            raise InvalidActivityTemplate(
                "parameter relations must be ParameterRelation values"
            )
        if len(set(relations)) != len(relations):
            raise InvalidActivityTemplate(
                "parameter relations must be unique"
            )
        relation_parameters = {
            name for relation in relations for name in relation.parameters
        }
        unknown_relation_parameters = sorted(relation_parameters - set(parameters))
        if unknown_relation_parameters:
            raise InvalidActivityTemplate(
                "parameter relation references unknown parameter "
                f"'{unknown_relation_parameters[0]}'"
            )

        errors = _validated_ids(self.error_pattern_ids, label="error pattern")
        hints = _validated_ids(self.hint_ids, label="hint")

        object.__setattr__(
            self,
            "parameter_bounds",
            MappingProxyType(dict(sorted(parameters.items()))),
        )
        object.__setattr__(self, "parameter_relations", relations)
        object.__setattr__(self, "error_pattern_ids", errors)
        object.__setattr__(self, "hint_ids", hints)

    def allows_parameter_values(self, values: Mapping[str, int]) -> bool:
        """Check exact parameter names, integer bounds, and reviewed relations."""

        if not isinstance(values, Mapping) or set(values) != set(
            self.parameter_bounds
        ):
            return False
        for name, bounds in self.parameter_bounds.items():
            value = values[name]
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not bounds.minimum <= value <= bounds.maximum
            ):
                return False
        return all(
            relation.is_satisfied(values) for relation in self.parameter_relations
        )


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
