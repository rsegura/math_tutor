import math

import pytest

from math_tutor.domain.regulation import (
    ConfidenceBand,
    ConversationalSignal,
    PedagogicalStrategy,
    RegulationPolicy,
    compatible_strategies,
)


def test_regulation_contracts_expose_only_reviewed_closed_values() -> None:
    assert {signal.value for signal in ConversationalSignal} == {
        "confused", "frustrated", "task-rejecting", "off-task",
        "requesting-help", "requesting-pause",
    }
    assert {strategy.value for strategy in PedagogicalStrategy} == {
        "repeat-instruction", "simplify-language", "give-ordered-hint",
        "redirect-gently", "validate-emotion", "take-short-pause",
    }
    with pytest.raises(ValueError):
        ConversationalSignal("engaged")


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        (0.0, ConfidenceBand.LOW),
        (0.499999, ConfidenceBand.LOW),
        (0.5, ConfidenceBand.MEDIUM),
        (0.799999, ConfidenceBand.MEDIUM),
        (0.8, ConfidenceBand.HIGH),
        (1.0, ConfidenceBand.HIGH),
    ],
)
def test_confidence_band_uses_exact_finite_boundaries(confidence, expected) -> None:
    assert ConfidenceBand.from_confidence(confidence) is expected


@pytest.mark.parametrize("confidence", [True, False, -0.01, 1.01, math.nan, math.inf, -math.inf, "0.8"])
def test_confidence_band_rejects_non_numeric_non_finite_or_out_of_range_values(confidence) -> None:
    with pytest.raises(ValueError, match="invalid-regulation-confidence"):
        ConfidenceBand.from_confidence(confidence)


def test_signal_strategy_compatibility_is_deterministic() -> None:
    assert compatible_strategies(ConversationalSignal.CONFUSED) == (
        PedagogicalStrategy.REPEAT_INSTRUCTION,
        PedagogicalStrategy.SIMPLIFY_LANGUAGE,
        PedagogicalStrategy.GIVE_ORDERED_HINT,
    )
    assert compatible_strategies(ConversationalSignal.OFF_TASK) == (
        PedagogicalStrategy.REDIRECT_GENTLY,
    )
    assert compatible_strategies(ConversationalSignal.REQUESTING_PAUSE) == (
        PedagogicalStrategy.TAKE_SHORT_PAUSE,
    )


def test_policy_is_immutable_and_total_for_every_signal() -> None:
    policy = RegulationPolicy(
        allowed_strategies=(
            PedagogicalStrategy.REPEAT_INSTRUCTION,
            PedagogicalStrategy.VALIDATE_EMOTION,
            PedagogicalStrategy.REDIRECT_GENTLY,
            PedagogicalStrategy.TAKE_SHORT_PAUSE,
        ),
        max_consecutive_regulation_turns=4,
    )
    assert all(policy.allowed_for(signal) for signal in ConversationalSignal)
    with pytest.raises((AttributeError, TypeError)):
        policy.max_consecutive_regulation_turns = 5


def test_policy_rejects_a_strategy_set_that_leaves_any_signal_unsupported() -> None:
    with pytest.raises(ValueError, match="incomplete-regulation-policy"):
        RegulationPolicy(
            allowed_strategies=(PedagogicalStrategy.REPEAT_INSTRUCTION,),
            max_consecutive_regulation_turns=4,
        )


def test_policy_rejects_duplicate_or_unknown_strategies() -> None:
    with pytest.raises(ValueError, match="duplicate-regulation-strategy"):
        RegulationPolicy(
            allowed_strategies=(PedagogicalStrategy.TAKE_SHORT_PAUSE,) * 2,
            max_consecutive_regulation_turns=4,
        )
    with pytest.raises(ValueError, match="invalid-regulation-strategy"):
        RegulationPolicy(
            allowed_strategies=("take-short-pause",),
            max_consecutive_regulation_turns=4,
        )
