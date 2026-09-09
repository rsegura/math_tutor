"""Provider-neutral contracts for bounded conversational regulation."""

from dataclasses import dataclass
from enum import Enum
import math
from types import MappingProxyType
from typing import Mapping


class ConversationalSignal(Enum):
    CONFUSED = "confused"
    FRUSTRATED = "frustrated"
    TASK_REJECTING = "task-rejecting"
    OFF_TASK = "off-task"
    REQUESTING_HELP = "requesting-help"
    REQUESTING_PAUSE = "requesting-pause"


class PedagogicalStrategy(Enum):
    REPEAT_INSTRUCTION = "repeat-instruction"
    SIMPLIFY_LANGUAGE = "simplify-language"
    GIVE_ORDERED_HINT = "give-ordered-hint"
    REDIRECT_GENTLY = "redirect-gently"
    VALIDATE_EMOTION = "validate-emotion"
    TAKE_SHORT_PAUSE = "take-short-pause"


class ExecutedRegulationAction(Enum):
    """What deterministic application code actually executed."""

    REPEAT_INSTRUCTION = "repeat-instruction"
    SIMPLIFY_LANGUAGE = "simplify-language"
    GIVE_ORDERED_HINT = "give-ordered-hint"
    REDIRECT_GENTLY = "redirect-gently"
    VALIDATE_EMOTION = "validate-emotion"
    TAKE_SHORT_PAUSE = "take-short-pause"
    CAP_CHOICE = "cap-choice"


class ConfidenceBand(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @classmethod
    def from_confidence(cls, confidence: object) -> "ConfidenceBand":
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise ValueError("invalid-regulation-confidence")
        if confidence < 0.5:
            return cls.LOW
        if confidence < 0.8:
            return cls.MEDIUM
        return cls.HIGH


_COMPATIBILITY: Mapping[ConversationalSignal, tuple[PedagogicalStrategy, ...]] = MappingProxyType(
    {
        ConversationalSignal.CONFUSED: (
            PedagogicalStrategy.REPEAT_INSTRUCTION,
            PedagogicalStrategy.SIMPLIFY_LANGUAGE,
            PedagogicalStrategy.GIVE_ORDERED_HINT,
        ),
        ConversationalSignal.REQUESTING_HELP: (
            PedagogicalStrategy.REPEAT_INSTRUCTION,
            PedagogicalStrategy.SIMPLIFY_LANGUAGE,
            PedagogicalStrategy.GIVE_ORDERED_HINT,
        ),
        ConversationalSignal.FRUSTRATED: (
            PedagogicalStrategy.VALIDATE_EMOTION,
            PedagogicalStrategy.SIMPLIFY_LANGUAGE,
            PedagogicalStrategy.TAKE_SHORT_PAUSE,
        ),
        ConversationalSignal.TASK_REJECTING: (
            PedagogicalStrategy.VALIDATE_EMOTION,
            PedagogicalStrategy.REDIRECT_GENTLY,
            PedagogicalStrategy.TAKE_SHORT_PAUSE,
        ),
        ConversationalSignal.OFF_TASK: (PedagogicalStrategy.REDIRECT_GENTLY,),
        ConversationalSignal.REQUESTING_PAUSE: (PedagogicalStrategy.TAKE_SHORT_PAUSE,),
    }
)


def compatible_strategies(signal: ConversationalSignal) -> tuple[PedagogicalStrategy, ...]:
    if not isinstance(signal, ConversationalSignal):
        raise ValueError("invalid-conversational-signal")
    return _COMPATIBILITY[signal]


@dataclass(frozen=True, slots=True)
class RegulationPolicy:
    allowed_strategies: tuple[PedagogicalStrategy, ...]
    max_consecutive_regulation_turns: int = 4

    def __post_init__(self) -> None:
        strategies = tuple(self.allowed_strategies)
        if any(not isinstance(strategy, PedagogicalStrategy) for strategy in strategies):
            raise ValueError("invalid-regulation-strategy")
        if len(set(strategies)) != len(strategies):
            raise ValueError("duplicate-regulation-strategy")
        if isinstance(self.max_consecutive_regulation_turns, bool) or not isinstance(
            self.max_consecutive_regulation_turns, int
        ) or not 1 <= self.max_consecutive_regulation_turns <= 12:
            raise ValueError("invalid-regulation-turn-cap")
        if any(not set(strategies).intersection(compatible_strategies(signal)) for signal in ConversationalSignal):
            raise ValueError("incomplete-regulation-policy")
        object.__setattr__(self, "allowed_strategies", strategies)

    def allowed_for(self, signal: ConversationalSignal) -> tuple[PedagogicalStrategy, ...]:
        allowed = set(self.allowed_strategies)
        return tuple(strategy for strategy in compatible_strategies(signal) if strategy in allowed)
