"""Deterministic execution helpers for reviewed conversation regulation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from math_tutor.application.ports import ActivityProgress
from math_tutor.domain.activities import Activity
from math_tutor.domain.regulation import (
    ConversationalSignal, ExecutedRegulationAction, PedagogicalStrategy,
    RegulationPolicy,
)

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
    executed_action: "ExecutedRegulationAction"
    regulation_revision: int


@dataclass(frozen=True, slots=True)
class RegulationDecisionPlan:
    speech: str
    executed_action: ExecutedRegulationAction
    hint: HintSelection | None = None


class CanonicalHintTextMissing(ValueError):
    pass


class NoAuthorisedRegulationFallback(ValueError):
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


def plan_regulation_response(
    *,
    signal: ConversationalSignal,
    requested_strategy: PedagogicalStrategy | None,
    policy: RegulationPolicy,
    consecutive_turns: int,
    activity: Activity,
    progress: ActivityProgress,
    max_hints: int,
    reviewed_hint_texts: Mapping[str, str],
    presentation: tuple[str, ...],
    adaptations: tuple[str, ...],
) -> RegulationDecisionPlan:
    """Choose one authorised, reviewed response for every regulation entry path."""

    if not isinstance(policy, RegulationPolicy):
        raise NoAuthorisedRegulationFallback("invalid-regulation-policy")
    allowed = set(policy.allowed_for(signal))
    if requested_strategy is None:
        requested_strategy = next((candidate for candidate in (
            PedagogicalStrategy.GIVE_ORDERED_HINT,
            PedagogicalStrategy.SIMPLIFY_LANGUAGE,
            PedagogicalStrategy.REPEAT_INSTRUCTION,
        ) if candidate in allowed), None)
        if requested_strategy is None:
            raise NoAuthorisedRegulationFallback("no-authorised-help-fallback")
    elif requested_strategy not in allowed:
        raise NoAuthorisedRegulationFallback("strategy-not-authorised")
    if consecutive_turns >= policy.max_consecutive_regulation_turns:
        return RegulationDecisionPlan(
            "¿Quieres continuar o hacer una pausa?",
            ExecutedRegulationAction.CAP_CHOICE,
        )

    strategy = requested_strategy
    if strategy is PedagogicalStrategy.GIVE_ORDERED_HINT:
        try:
            hint = select_next_reviewed_hint(
                activity, progress, max_hints=max_hints,
                reviewed_hint_texts=reviewed_hint_texts,
            )
        except CanonicalHintTextMissing:
            hint = None
        if hint is not None:
            return RegulationDecisionPlan(
                f"{hint.speech} {activity.prompt_es}",
                ExecutedRegulationAction.GIVE_ORDERED_HINT,
                hint,
            )
        strategy = next((candidate for candidate in (
            PedagogicalStrategy.SIMPLIFY_LANGUAGE,
            PedagogicalStrategy.REPEAT_INSTRUCTION,
        ) if candidate in allowed), None)
        if strategy is None:
            raise NoAuthorisedRegulationFallback("no-authorised-help-fallback")

    speech = canonical_regulation_speech(
        strategy, prompt=activity.prompt_es, presentation=presentation,
        adaptations=adaptations,
    )
    return RegulationDecisionPlan(speech, ExecutedRegulationAction(strategy.value))
