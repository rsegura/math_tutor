"""Deterministic execution helpers for reviewed conversation regulation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from math_tutor.application.ports import ActivityProgress
from math_tutor.domain.activities import Activity
from math_tutor.domain.regulation import PedagogicalStrategy


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


def canonical_regulation_speech(strategy: PedagogicalStrategy, *, prompt: str) -> str:
    if strategy is PedagogicalStrategy.REPEAT_INSTRUCTION:
        return prompt
    if strategy is PedagogicalStrategy.SIMPLIFY_LANGUAGE:
        return f"Vamos paso a paso. {prompt}"
    if strategy is PedagogicalStrategy.REDIRECT_GENTLY:
        return f"Volvamos juntos a la actividad. {prompt}"
    if strategy is PedagogicalStrategy.VALIDATE_EMOTION:
        return "Entiendo que puede ser difícil. Vamos paso a paso."
    if strategy is PedagogicalStrategy.TAKE_SHORT_PAUSE:
        return "Hacemos una pausa corta. Cuando estés preparado, seguimos."
    raise ValueError("canonical-regulation-content-missing")
