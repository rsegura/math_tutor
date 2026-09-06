"""Provider-neutral voice turn policy at the STT/harness seam."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import Callable
import asyncio
import inspect
import unicodedata

from livekit.agents import Agent, stt
from livekit.agents import llm as livekit_llm


CONFIRMATION_ES = "No estoy seguro de haberte oído bien. ¿Puedes repetirlo?"
STOP_ES = "De acuerdo, paramos aquí."


@dataclass(frozen=True, slots=True)
class VoiceTurn:
    turn_id: str
    text: str
    confidence: float | None
    _cancelled: Event

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()


@dataclass(frozen=True, slots=True)
class VoiceDecision:
    speech: str
    terminal: bool = False
    needs_confirmation: bool = False
    reason: str = "accepted"


def _normalise(text: str) -> str:
    return "".join(character for character in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(character))


def is_stop_request(text: str) -> bool:
    value = _normalise(text)
    phrases = ("quiero parar", "para ya", "paramos", "no quiero seguir", "terminar")
    return any(phrase in value for phrase in phrases)


class TurnCoordinator:
    """Correlates one active generation and drops interrupted completions."""

    def __init__(self, handler: Callable[[VoiceTurn], str], *, confidence_threshold: float = 0.65) -> None:
        self._handler = handler
        self._threshold = confidence_threshold
        self._active: VoiceTurn | None = None

    @property
    def active_turn_id(self) -> str | None:
        return self._active.turn_id if self._active else None

    def begin_turn(self, turn_id: str, text: str, *, confidence: float | None) -> VoiceTurn:
        if self._active is not None:
            self._active._cancelled.set()
        self._active = VoiceTurn(turn_id, text, confidence, Event())
        return self._active

    def complete_turn(self, turn_id: str, speech: str) -> VoiceDecision | None:
        if self._active is None or self._active.turn_id != turn_id or self._active.cancelled:
            return None
        self._active = None
        return VoiceDecision(speech)

    def handle_turn(self, turn_id: str, text: str, *, confidence: float | None) -> VoiceDecision:
        turn = self.begin_turn(turn_id, text, confidence=confidence)
        if is_stop_request(text):
            self._active = None
            return VoiceDecision(STOP_ES, terminal=True, reason="stop-requested")
        if confidence is None or confidence < self._threshold:
            self._active = None
            return VoiceDecision(CONFIRMATION_ES, needs_confirmation=True, reason="low-stt-confidence")
        speech = self._handler(turn)
        return self.complete_turn(turn_id, speech) or VoiceDecision("", reason="interrupted")


class SilentLLM(livekit_llm.LLM):
    """Scheduling sentinel: all model calls must instead cross the harness."""
    def chat(self, *args, **kwargs):
        raise RuntimeError("direct model access is forbidden; use the pedagogical harness")


class HarnessVoiceAgent(Agent):
    """LiveKit transport adapter whose generated speech comes only from the harness."""
    def __init__(self, *, instructions: str, initial_prompt: str, decide: Callable[[VoiceTurn], VoiceDecision], cancel: Callable[[], None], confidence_threshold: float = 0.65, tts_watchdog=None, initial_terminal_reason: str | None = None, force_stop: Callable[[str], None] | None = None, fallback_audio=None, terminal_handle: Callable[[], object | None] | None = None) -> None:
        super().__init__(instructions=instructions)
        self._decide = decide
        self._cancel = cancel
        self._threshold = confidence_threshold
        self._initial_prompt = initial_prompt
        self._pending = None
        self._confidence = None
        self._closer = None
        self._tts_watchdog = tts_watchdog
        self._initial_terminal_reason = initial_terminal_reason
        self._force_stop = force_stop
        self._fallback_audio = fallback_audio
        self._terminal_handle = terminal_handle or (lambda: None)
        self._tts_fallback_started = False
        self._tts_terminal_pending = None

    def bind_terminal_closer(self, closer) -> None:
        self._closer = closer

    async def on_enter(self) -> None:
        await self.session.say(self._initial_prompt)
        if self._initial_terminal_reason is not None and self._closer is not None:
            self._closer.trigger(self._initial_terminal_reason)

    async def stt_node(self, audio, model_settings):
        async for event in Agent.default.stt_node(self, audio, model_settings):
            if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT and event.alternatives:
                value = event.alternatives[0].confidence
                self._confidence = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value <= 1 else None
            yield event

    async def tts_node(self, text, model_settings):
        source = Agent.default.tts_node(self, text, model_settings)
        if self._tts_watchdog is None:
            async for frame in source:
                yield frame
            return
        try:
            async for frame in self._tts_watchdog.iterate(source,on_terminal=self._on_tts_terminal):
                yield frame
        finally:
            pending, self._tts_terminal_pending = self._tts_terminal_pending, None
            if pending is not None and self._closer is not None:
                reason, handle = pending
                self._closer.trigger(reason, handle)

    async def _on_tts_terminal(self, reason: str, speech: str) -> None:
        if self._tts_fallback_started:
            return
        self._tts_fallback_started = True
        try:
            if self._force_stop is not None:
                await asyncio.to_thread(self._force_stop, reason)
            handle = self._fallback_audio.enqueue() if self._fallback_audio is not None else None
            self._tts_terminal_pending = (reason, handle)
        except BaseException:
            if self._closer is not None:
                self._closer.trigger(reason)
            raise

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        self._cancel()
        self._pending = VoiceTurn(
            getattr(new_message, "id", None) or "uncorrelated-turn",
            (getattr(new_message, "text_content", None) or "").strip(),
            self._confidence,
            Event(),
        )
        self._confidence = None

    async def llm_node(self, chat_ctx, tools, model_settings):
        if tools:
            raise RuntimeError("LiveKit tools are forbidden; tools belong to the pedagogical harness")
        turn, self._pending = self._pending, None
        newest = next((getattr(item, "text_content", None) for item in reversed(getattr(chat_ctx, "items", ()) or ()) if getattr(item, "role", None) == "user"), None)
        if turn is None or not turn.text or _normalise(newest or "") != _normalise(turn.text):
            yield CONFIRMATION_ES
            return
        if is_stop_request(turn.text):
            try:
                value = self._decide(turn); decision = await value if inspect.isawaitable(value) else value
            except asyncio.CancelledError:
                self._cancel()
                raise
        elif turn.confidence is None or turn.confidence < self._threshold:
            decision = VoiceDecision(CONFIRMATION_ES, needs_confirmation=True, reason="low-stt-confidence")
        else:
            try:
                value = self._decide(turn); decision = await value if inspect.isawaitable(value) else value
            except asyncio.CancelledError:
                self._cancel()
                raise
        try:
            if decision.speech:
                yield decision.speech
        finally:
            if decision.terminal and self._closer is not None:
                self._closer.trigger(decision.reason, self._terminal_handle())
