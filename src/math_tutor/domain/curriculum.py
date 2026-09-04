"""Immutable curriculum objectives and their prerequisite graph."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from heapq import heapify, heappop, heappush
from types import MappingProxyType
from typing import AbstractSet, Mapping


class InvalidCurriculum(ValueError):
    """Raised when curriculum data does not form a valid competency graph."""


class CurriculumBand(Enum):
    """Indicative primary-school progression bands."""

    INITIAL = "initial"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


def _require_trimmed(value: object, *, label: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise InvalidCurriculum(f"{label} must be a trimmed, nonempty string")


def _validated_ids(values: Iterable[str], *, label: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise InvalidCurriculum(f"{label} ids must be a non-scalar sequence")
    result = tuple(values)
    for value in result:
        _require_trimmed(value, label=label)
    if len(set(result)) != len(result):
        raise InvalidCurriculum(f"duplicate {label} id")
    return result


@dataclass(frozen=True, slots=True)
class HintDefinition:
    """One reviewed hint in a least-to-most assistance sequence."""

    id: str
    text: str
    order: int

    def __post_init__(self) -> None:
        _require_trimmed(self.id, label="hint id")
        _require_trimmed(self.text, label="hint text")
        if (
            isinstance(self.order, bool)
            or not isinstance(self.order, int)
            or self.order < 1
        ):
            raise InvalidCurriculum("hint order must be an integer of at least 1")


@dataclass(frozen=True, slots=True)
class LearningObjective:
    """A reviewed learning objective and its curriculum references."""

    id: str
    band: CurriculumBand
    title: str
    instruction: str
    prerequisite_ids: tuple[str, ...]
    activity_family_ids: tuple[str, ...]
    known_error_pattern_ids: tuple[str, ...]
    hints: tuple[HintDefinition, ...]

    def __post_init__(self) -> None:
        _require_trimmed(self.id, label="objective id")
        if not isinstance(self.band, CurriculumBand):
            raise InvalidCurriculum("objective band must be a CurriculumBand")
        _require_trimmed(self.title, label="objective title")
        _require_trimmed(self.instruction, label="objective instruction")

        prerequisites = _validated_ids(
            self.prerequisite_ids, label="prerequisite"
        )
        families = _validated_ids(
            self.activity_family_ids, label="activity family"
        )
        error_patterns = _validated_ids(
            self.known_error_pattern_ids, label="error pattern"
        )
        hints = tuple(self.hints)

        if not families:
            raise InvalidCurriculum(
                "an objective must have at least one activity family"
            )
        if not hints:
            raise InvalidCurriculum("an objective must have at least one hint")
        if not all(
            isinstance(definition, HintDefinition) for definition in hints
        ):
            raise InvalidCurriculum("objective hints must be HintDefinition values")

        hint_ids = tuple(definition.id for definition in hints)
        if len(set(hint_ids)) != len(hint_ids):
            raise InvalidCurriculum("duplicate hint id in objective")
        actual_orders = tuple(definition.order for definition in hints)
        expected_orders = tuple(range(1, len(hints) + 1))
        if actual_orders != expected_orders:
            raise InvalidCurriculum(
                "hint order must be contiguous and start at 1"
            )

        object.__setattr__(self, "prerequisite_ids", prerequisites)
        object.__setattr__(self, "activity_family_ids", families)
        object.__setattr__(self, "known_error_pattern_ids", error_patterns)
        object.__setattr__(self, "hints", hints)


@dataclass(frozen=True, slots=True)
class CurriculumCatalog:
    """Indexed, validated collection of learning objectives."""

    objectives: tuple[LearningObjective, ...]
    topological_order: tuple[str, ...] = field(init=False)
    _objectives_by_id: Mapping[str, LearningObjective] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        objectives = tuple(self.objectives)
        if not all(isinstance(item, LearningObjective) for item in objectives):
            raise InvalidCurriculum(
                "catalog entries must be LearningObjective values"
            )

        by_id: dict[str, LearningObjective] = {}
        for definition in objectives:
            if definition.id in by_id:
                raise InvalidCurriculum(
                    f"duplicate objective id '{definition.id}'"
                )
            by_id[definition.id] = definition

        for definition in objectives:
            for prerequisite_id in definition.prerequisite_ids:
                if prerequisite_id == definition.id:
                    raise InvalidCurriculum(
                        f"objective '{definition.id}' cannot require itself"
                    )
                if prerequisite_id not in by_id:
                    raise InvalidCurriculum(
                        f"objective '{definition.id}' has unknown prerequisite "
                        f"'{prerequisite_id}'"
                    )

        topological_order = self._topological_order(by_id)
        object.__setattr__(self, "objectives", objectives)
        object.__setattr__(self, "topological_order", topological_order)
        object.__setattr__(self, "_objectives_by_id", MappingProxyType(by_id))

    @staticmethod
    def _topological_order(
        by_id: Mapping[str, LearningObjective],
    ) -> tuple[str, ...]:
        remaining_prerequisites = {
            objective_id: len(definition.prerequisite_ids)
            for objective_id, definition in by_id.items()
        }
        dependents: dict[str, list[str]] = {
            objective_id: [] for objective_id in by_id
        }
        for objective_id, definition in by_id.items():
            for prerequisite_id in definition.prerequisite_ids:
                dependents[prerequisite_id].append(objective_id)

        ready = [
            objective_id
            for objective_id, count in remaining_prerequisites.items()
            if count == 0
        ]
        heapify(ready)
        ordered: list[str] = []
        while ready:
            objective_id = heappop(ready)
            ordered.append(objective_id)
            for dependent_id in dependents[objective_id]:
                remaining_prerequisites[dependent_id] -= 1
                if remaining_prerequisites[dependent_id] == 0:
                    heappush(ready, dependent_id)

        if len(ordered) != len(by_id):
            raise InvalidCurriculum("curriculum prerequisite graph contains a cycle")
        return tuple(ordered)

    def objective(self, objective_id: str) -> LearningObjective:
        """Return an objective by its stable identifier."""

        try:
            return self._objectives_by_id[objective_id]
        except KeyError as error:
            raise InvalidCurriculum(
                f"unknown objective '{objective_id}'"
            ) from error

    def prerequisites_met(
        self,
        objective_id: str,
        achieved_objective_ids: AbstractSet[str],
    ) -> bool:
        """Return whether every direct prerequisite has been achieved."""

        definition = self.objective(objective_id)
        return all(
            prerequisite_id in achieved_objective_ids
            for prerequisite_id in definition.prerequisite_ids
        )
