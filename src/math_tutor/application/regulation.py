"""Deterministic execution helpers for reviewed conversation regulation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from math_tutor.application.ports import ActivityProgress
from math_tutor.domain.activities import Activity
from math_tutor.domain.regulation import PedagogicalStrategy

_PRESENTATION_VALUES = frozenset({
    "concrete-and-playful", "clear-and-encouraging", "age-respectful",
    "short", "medium", "short-instructions",
})
_ADAPTATION_VALUES = frozenset({
    "short-instructions", "slow-pace", "extra-repetition",
    "concrete-examples", "reduced-choice",
})


@dataclass(frozen=True, slots=True)
class HintSelection:
    hint_id: str
    speech: str


@dataclass(frozen=True, slots=True)
class RegulationResult:
    speech: str
    strategy: PedagogicalStrategy
    regulation_revision: int


class CanonicalHintTextMissing(ValueError):
    pass


def select_next_reviewed_hint(
    activity: Activity,
    progress: ActivityProgress,
    *,
    max_hints: int,
    reviewed_hint_texts: Mapping[str, str],
) -> HintSelection | None:
    index = progress.hints_used
    if index >= max_hints or index >= len(activity.hint_ids):
        return None
    hint_id = activity.hint_ids[index]
    speech = reviewed_hint_texts.get(hint_id)
    if not isinstance(speech, str) or not speech.strip():
        raise CanonicalHintTextMissing("canonical-hint-text-missing")
    return HintSelection(hint_id, speech)


def canonical_regulation_speech(
    strategy: PedagogicalStrategy,
    *,
    prompt: str,
    presentation: tuple[str, ...] = (),
    adaptations: tuple[str, ...] = (),
) -> str:
    if (
        not isinstance(presentation, tuple)
        or not isinstance(adaptations, tuple)
        or len(set(presentation)) != len(presentation)
        or len(set(adaptations)) != len(adaptations)
        or any(value not in _PRESENTATION_VALUES for value in presentation)
        or any(value not in _ADAPTATION_VALUES for value in adaptations)
    ):
        raise ValueError("invalid-regulation-presentation")
    concise = "short-instructions" in presentation or "short-instructions" in adaptations
    if strategy is PedagogicalStrategy.REPEAT_INSTRUCTION:
        if "extra-repetition" in adaptations:
            return f"Te lo repito. {prompt}"
        return prompt
    if strategy is PedagogicalStrategy.SIMPLIFY_LANGUAGE:
        if concise:
            return f"Vamos despacio. {prompt}"
        return f"Vamos paso a paso. {prompt}"
    if strategy is PedagogicalStrategy.REDIRECT_GENTLY:
        return f"Volvamos juntos a la actividad. {prompt}"
    if strategy is PedagogicalStrategy.VALIDATE_EMOTION:
        if "concrete-and-playful" in presentation:
            return "Está bien que cueste. Lo hacemos juntos."
        return "Entiendo que puede ser difícil. Vamos paso a paso."
    if strategy is PedagogicalStrategy.TAKE_SHORT_PAUSE:
        return "Hacemos una pausa corta. Cuando estés preparado, seguimos."
    raise ValueError("canonical-regulation-content-missing")
