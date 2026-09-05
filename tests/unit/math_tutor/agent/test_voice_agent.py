import asyncio
from types import SimpleNamespace
from threading import Event

from math_tutor.agent.voice_agent import HarnessVoiceAgent, TurnCoordinator, VoiceDecision, VoiceTurn


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


def agent(decide, cancel=lambda:None):
    return HarnessVoiceAgent(instructions="bounded",initial_prompt="Pregunta",decide=decide,cancel=cancel)


async def output(node): return [item async for item in node]


def chat(text): return SimpleNamespace(items=[SimpleNamespace(role="user",text_content=text)])


async def test_harness_agent_rejects_uncorrelated_and_low_confidence_turns_without_model():
    calls=[]; value=agent(lambda turn:calls.append(turn) or VoiceDecision("modelo"))
    value._pending=VoiceTurn("t","uno",.9,Event())
    assert await output(value.llm_node(chat("dos"),[],None)) == ["No estoy seguro de haberte oído bien. ¿Puedes repetirlo?"]
    value._pending=VoiceTurn("t2","uno",.2,Event())
    assert await output(value.llm_node(chat("uno"),[],None)) == ["No estoy seguro de haberte oído bien. ¿Puedes repetirlo?"]
    assert calls == []


async def test_terminal_closer_triggers_when_terminal_yield_is_interrupted():
    reasons=[]; value=agent(lambda turn:VoiceDecision("Paramos",terminal=True,reason="stop"))
    value.bind_terminal_closer(SimpleNamespace(trigger=lambda reason:reasons.append(reason)))
    value._pending=VoiceTurn("t","quiero parar",.1,Event())
    node=value.llm_node(chat("quiero parar"),[],None)
    assert await anext(node) == "Paramos"
    await node.aclose()
    assert reasons == ["stop"]


async def test_interrupted_harness_generation_cancels_authoritative_generation():
    entered=Event(); release=Event(); cancellations=[]
    def decide(turn): entered.set(); release.wait(1); return VoiceDecision("late")
    value=agent(decide,lambda:cancellations.append(True)); value._pending=VoiceTurn("t","uno",.9,Event())
    task=asyncio.create_task(anext(value.llm_node(chat("uno"),[],None)))
    await asyncio.to_thread(entered.wait,1)
    task.cancel()
    with __import__('pytest').raises(asyncio.CancelledError): await task
    release.set()
    assert cancellations == [True]
