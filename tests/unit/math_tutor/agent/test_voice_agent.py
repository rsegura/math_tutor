import asyncio

from math_tutor.agent.voice_agent import TurnCoordinator


def test_low_confidence_turn_requests_confirmation_without_model_call():
    calls = []
    coordinator = TurnCoordinator(lambda turn: calls.append(turn) or "modelo")

    result = coordinator.handle_turn("t-1", "doce", confidence=0.31)

    assert result.speech == "No estoy seguro de haberte oído bien. ¿Puedes repetirlo?"
    assert result.needs_confirmation is True
    assert calls == []


def test_stop_has_priority_even_when_transcription_confidence_is_low():
    coordinator = TurnCoordinator(lambda turn: "no debe usarse")

    result = coordinator.handle_turn("t-2", "quiero parar", confidence=0.1)

    assert result.terminal is True
    assert result.reason == "stop-requested"


def test_new_turn_cancels_the_prior_generation():
    coordinator = TurnCoordinator(lambda turn: turn.text)
    first = coordinator.begin_turn("one", "uno", confidence=1.0)
    second = coordinator.begin_turn("two", "dos", confidence=1.0)

    assert first.cancelled is True
    assert second.cancelled is False
    assert coordinator.active_turn_id == "two"


def test_completion_is_correlated_to_the_active_turn():
    coordinator = TurnCoordinator(lambda turn: turn.text)
    coordinator.begin_turn("one", "uno", confidence=1.0)
    coordinator.begin_turn("two", "dos", confidence=1.0)

    assert coordinator.complete_turn("one", "antiguo") is None
    assert coordinator.complete_turn("two", "actual").speech == "actual"

