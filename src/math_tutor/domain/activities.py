"""Deterministic construction of activities from reviewed templates.

This module deliberately accepts structured template contracts only.  Speech or
free-text interpretation belongs outside the educational domain.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from itertools import product
from types import MappingProxyType
from typing import Mapping, TypeAlias

from math_tutor.domain.templates import (
    ActivityTemplate,
    AnswerConstraint,
    AnswerDerivation,
    AnswerDerivationOperation,
    ExpectedAnswerKind,
)


ScalarAnswer: TypeAlias = int | str
AnswerValue: TypeAlias = ScalarAnswer | tuple[int, ...]


class InvalidActivity(ValueError):
    """Raised when an activity cannot be produced from its reviewed contract."""


class AnswerInputStatus(Enum):
    """Status assigned by the upstream structured-answer interpreter."""

    EVALUABLE = "evaluable"
    AMBIGUOUS = "ambiguous"
    NOT_EVALUABLE = "not-evaluable"


@dataclass(frozen=True, slots=True)
class StructuredAnswer:
    """An interpreter result containing no natural-language parsing logic."""

    kind: ExpectedAnswerKind | None
    values: Mapping[str, AnswerValue]
    status: AnswerInputStatus = AnswerInputStatus.EVALUABLE

    def __post_init__(self) -> None:
        if not isinstance(self.status, AnswerInputStatus):
            raise InvalidActivity("answer status must be an AnswerInputStatus")
        if self.status is AnswerInputStatus.EVALUABLE and not isinstance(
            self.kind, ExpectedAnswerKind
        ):
            raise InvalidActivity("an evaluable answer requires a typed kind")
        if not isinstance(self.values, Mapping):
            raise InvalidActivity("answer values must be a mapping")
        frozen: dict[str, AnswerValue] = {}
        for field, value in self.values.items():
            if not isinstance(field, str) or not field:
                raise InvalidActivity("answer field names must be nonempty strings")
            frozen[field] = tuple(value) if isinstance(value, list) else value
        object.__setattr__(self, "values", MappingProxyType(frozen))

    @classmethod
    def evaluable(
        cls,
        kind: ExpectedAnswerKind,
        values: Mapping[str, AnswerValue],
    ) -> StructuredAnswer:
        """Build an answer that deterministic mathematics may evaluate."""

        return cls(kind=kind, values=values)

    @classmethod
    def ambiguous(cls) -> StructuredAnswer:
        """Represent multiple plausible interpretations without choosing one."""

        return cls(
            kind=None,
            values={},
            status=AnswerInputStatus.AMBIGUOUS,
        )

    @classmethod
    def not_evaluable(cls) -> StructuredAnswer:
        """Represent input from which no structured answer could be recovered."""

        return cls(
            kind=None,
            values={},
            status=AnswerInputStatus.NOT_EVALUABLE,
        )


@dataclass(frozen=True, slots=True)
class Activity:
    """One concrete, fully reviewable learner activity."""

    template_id: str
    objective_id: str
    difficulty: int
    prompt_es: str
    parameters: Mapping[str, int]
    expected_answer: StructuredAnswer
    error_pattern_ids: tuple[str, ...]
    hint_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "parameters", MappingProxyType(dict(self.parameters))
        )
        object.__setattr__(self, "error_pattern_ids", tuple(self.error_pattern_ids))
        object.__setattr__(self, "hint_ids", tuple(self.hint_ids))


def _derive(
    derivation: AnswerDerivation, parameters: Mapping[str, int]
) -> AnswerValue:
    values = tuple(parameters[name] for name in derivation.parameters)
    operation = derivation.operation
    if operation is AnswerDerivationOperation.VALUE:
        return values[0]
    if operation is AnswerDerivationOperation.SUM:
        return sum(values)
    if operation is AnswerDerivationOperation.DIFFERENCE:
        return values[0] - values[1]
    if operation is AnswerDerivationOperation.DOUBLE:
        return values[0] * 2
    if operation is AnswerDerivationOperation.MINIMUM:
        return min(values)
    if operation is AnswerDerivationOperation.MAXIMUM:
        return max(values)
    if operation is AnswerDerivationOperation.RELATION:
        return "greater" if values[0] > values[1] else (
            "less" if values[0] < values[1] else "equal"
        )
    if operation is AnswerDerivationOperation.SUCCESSOR:
        return values[0] + 1
    if operation is AnswerDerivationOperation.PREDECESSOR:
        return values[0] - 1
    if operation is AnswerDerivationOperation.FOLLOWING_SEQUENCE:
        start, length = values
        return tuple(range(start + 1, start + length + 1))
    if operation is AnswerDerivationOperation.TENS_DIGIT:
        return values[0] // 10
    if operation is AnswerDerivationOperation.UNITS_DIGIT:
        return values[0] % 10
    if operation is AnswerDerivationOperation.TENS_VALUE:
        return (values[0] // 10) * 10
    if operation is AnswerDerivationOperation.COMPOSE_TENS_UNITS:
        return values[0] * 10 + values[1]
    raise InvalidActivity(f"unsupported derivation operation '{operation}'")


def _derive_answer(
    template: ActivityTemplate, parameters: Mapping[str, int]
) -> StructuredAnswer:
    values = {
        derivation.field: _derive(derivation, parameters)
        for derivation in template.expected_answer.derivations
    }
    return StructuredAnswer.evaluable(template.expected_answer.kind, values)


def _integer_values(answer: StructuredAnswer) -> tuple[int, ...]:
    flattened: list[int] = []
    for value in answer.values.values():
        if isinstance(value, tuple):
            flattened.extend(value)
        elif isinstance(value, int) and not isinstance(value, bool):
            flattened.append(value)
    return tuple(flattened)


def _referenced_parameter_values(
    template: ActivityTemplate, parameters: Mapping[str, int]
) -> tuple[int, ...]:
    return tuple(
        parameters[name]
        for derivation in template.expected_answer.derivations
        for name in derivation.parameters
    )


def _is_two_digit_contract_satisfied(
    template: ActivityTemplate,
    parameters: Mapping[str, int],
    answer: StructuredAnswer,
) -> bool:
    """Validate only the reviewed declarative forms that encode two digits."""

    derivations = template.expected_answer.derivations
    place_extractions = {
        AnswerDerivationOperation.TENS_DIGIT,
        AnswerDerivationOperation.UNITS_DIGIT,
        AnswerDerivationOperation.TENS_VALUE,
    }
    if derivations and all(
        item.operation in place_extractions and len(item.parameters) == 1
        for item in derivations
    ):
        return all(10 <= parameters[item.parameters[0]] <= 99 for item in derivations)

    if len(derivations) == 1:
        derivation = derivations[0]
        operands = tuple(parameters[name] for name in derivation.parameters)
        result = answer.values[derivation.field]
        if derivation.operation is AnswerDerivationOperation.COMPOSE_TENS_UNITS:
            return (
                len(operands) == 2
                and 1 <= operands[0] <= 9
                and 0 <= operands[1] <= 9
                and isinstance(result, int)
                and 10 <= result <= 99
            )
        if derivation.operation is AnswerDerivationOperation.SUM:
            return (
                len(operands) == 2
                and 10 <= operands[0] <= 90
                and operands[0] % 10 == 0
                and 0 <= operands[1] <= 9
                and isinstance(result, int)
                and 10 <= result <= 99
            )

    referenced = _referenced_parameter_values(template, parameters)
    return (
        len(derivations) == 2
        and all(
            derivation.operation is AnswerDerivationOperation.VALUE
            and len(derivation.parameters) == 1
            for derivation in derivations
        )
        and 1 <= referenced[0] <= 9
        and 0 <= referenced[1] <= 9
    )


def _has_no_carry(
    template: ActivityTemplate, parameters: Mapping[str, int]
) -> bool:
    additions = tuple(
        derivation
        for derivation in template.expected_answer.derivations
        if derivation.operation is AnswerDerivationOperation.SUM
    )

    def has_no_carry(operands: tuple[int, ...]) -> bool:
        if not operands or any(value < 0 for value in operands):
            return False
        remaining = operands
        while any(remaining):
            if sum(value % 10 for value in remaining) > 9:
                return False
            remaining = tuple(value // 10 for value in remaining)
        return True

    return bool(additions) and all(
        has_no_carry(tuple(parameters[name] for name in derivation.parameters))
        for derivation in additions
    )


def _has_no_borrow(
    template: ActivityTemplate, parameters: Mapping[str, int]
) -> bool:
    subtractions = tuple(
        derivation
        for derivation in template.expected_answer.derivations
        if derivation.operation is AnswerDerivationOperation.DIFFERENCE
    )

    def has_no_borrow(minuend: int, subtrahend: int) -> bool:
        if minuend < 0 or subtrahend < 0:
            return False
        while minuend or subtrahend:
            if minuend % 10 < subtrahend % 10:
                return False
            minuend //= 10
            subtrahend //= 10
        return True

    return bool(subtractions) and all(
        has_no_borrow(
            parameters[derivation.parameters[0]],
            parameters[derivation.parameters[1]],
        )
        for derivation in subtractions
    )


def _answer_satisfies_constraints(
    template: ActivityTemplate,
    parameters: Mapping[str, int],
    answer: StructuredAnswer,
) -> bool:
    raw_constraints = template.expected_answer.constraints
    unsupported = tuple(
        constraint
        for constraint in raw_constraints
        if not isinstance(constraint, AnswerConstraint)
    )
    if unsupported:
        name = repr(unsupported[0])
        raise InvalidActivity(f"unsupported answer constraint '{name}'")
    constraints = set(raw_constraints)

    values = _integer_values(answer)
    contextual_values = values or _referenced_parameter_values(template, parameters)
    if AnswerConstraint.NON_NEGATIVE in constraints and any(
        value < 0 for value in contextual_values
    ):
        return False
    if AnswerConstraint.WITHIN_10 in constraints and any(
        not 0 <= value <= 10 for value in contextual_values
    ):
        return False
    if AnswerConstraint.WITHIN_20 in constraints and any(
        not 0 <= value <= 20 for value in contextual_values
    ):
        return False
    if AnswerConstraint.TWO_DIGIT in constraints and not _is_two_digit_contract_satisfied(
        template, parameters, answer
    ):
        return False
    if AnswerConstraint.NO_CARRY in constraints and not _has_no_carry(
        template, parameters
    ):
        return False
    if AnswerConstraint.NO_BORROW in constraints and not _has_no_borrow(
        template, parameters
    ):
        return False
    sequence = next(
        (value for value in answer.values.values() if isinstance(value, tuple)),
        None,
    )
    if AnswerConstraint.ASCENDING in constraints and (
        sequence is None
        or any(left >= right for left, right in zip(sequence, sequence[1:]))
    ):
        return False
    if AnswerConstraint.DESCENDING in constraints and (
        sequence is None
        or any(left <= right for left, right in zip(sequence, sequence[1:]))
    ):
        return False
    if AnswerConstraint.ADJACENT in constraints and (
        sequence is None
        or any(abs(left - right) != 1 for left, right in zip(sequence, sequence[1:]))
    ):
        return False
    if AnswerConstraint.DISTINCT in constraints and len(contextual_values) != len(
        set(contextual_values)
    ):
        return False
    return True


def _valid_candidates(
    template: ActivityTemplate,
) -> tuple[tuple[dict[str, int], StructuredAnswer], ...]:
    names = tuple(template.parameter_bounds)
    ranges = tuple(
        range(bounds.minimum, bounds.maximum + 1)
        for bounds in (template.parameter_bounds[name] for name in names)
    )
    candidates: list[tuple[dict[str, int], StructuredAnswer]] = []
    for combination in product(*ranges):
        parameters = dict(zip(names, combination, strict=True))
        if not template.allows_parameter_values(parameters):
            continue
        answer = _derive_answer(template, parameters)
        if _answer_satisfies_constraints(template, parameters, answer):
            candidates.append((parameters, answer))
    candidates.sort(
        key=lambda item: (
            sum(abs(value) for value in item[0].values()),
            tuple(item[0].values()),
        )
    )
    return tuple(candidates)


def _difficulty_slice(
    candidates: tuple[tuple[dict[str, int], StructuredAnswer], ...],
    template: ActivityTemplate,
    difficulty: int,
) -> tuple[tuple[dict[str, int], StructuredAnswer], ...]:
    levels = template.difficulty_max - template.difficulty_min + 1
    level = difficulty - template.difficulty_min
    start = len(candidates) * level // levels
    stop = len(candidates) * (level + 1) // levels
    return candidates[start : max(start + 1, stop)]


def generate_activity(
    template: ActivityTemplate, *, seed: int, difficulty: int
) -> Activity:
    """Generate the same valid activity for the same template, seed and level."""

    if not isinstance(template, ActivityTemplate):
        raise InvalidActivity("template must be an ActivityTemplate")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise InvalidActivity("seed must be an integer")
    if (
        isinstance(difficulty, bool)
        or not isinstance(difficulty, int)
        or not template.difficulty_min <= difficulty <= template.difficulty_max
    ):
        raise InvalidActivity("difficulty is outside the reviewed template range")

    candidates = _valid_candidates(template)
    if not candidates:
        raise InvalidActivity(
            f"template '{template.id}' has no mathematically valid assignment"
        )
    candidates = _difficulty_slice(candidates, template, difficulty)
    digest = sha256(f"{template.id}:{seed}:{difficulty}".encode()).digest()
    parameters, answer = candidates[
        int.from_bytes(digest[:8], byteorder="big") % len(candidates)
    ]
    return Activity(
        template_id=template.id,
        objective_id=template.objective_id,
        difficulty=difficulty,
        prompt_es=template.prompt_template_es.format(**parameters),
        parameters=parameters,
        expected_answer=answer,
        error_pattern_ids=template.error_pattern_ids,
        hint_ids=template.hint_ids,
    )
