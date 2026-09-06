import asyncio
from types import SimpleNamespace

import pytest

from math_tutor.agent.lifecycle import FALLBACK_AUDIO_PATH, FALLBACK_AUDIO_TRANSCRIPT_PATH, TTS_FALLBACK_ES, StaticFallbackAudioPlayer, TTSWatchdog, TerminalCloser


class Source:
    def __init__(self, generator): self.generator=generator; self.closed=0
    def __aiter__(self): return self.generator
    async def aclose(self): self.closed += 1; await self.generator.aclose()


async def collect(watchdog, source, terminal):
    return [item async for item in watchdog.iterate(source,on_terminal=lambda reason,speech:terminal.append((reason,speech)))]


@pytest.mark.asyncio
async def test_watchdog_first_audio_timeout_is_terminal_and_closes_source():
    async def frames(): await asyncio.sleep(.1); yield "late"
    source=Source(frames()); terminal=[]
    assert await collect(TTSWatchdog(first_audio_seconds=.01,total_seconds=.2),source,terminal) == []
    assert terminal == [("tts-first-audio-timeout","No he podido reproducir el audio. Terminamos por ahora.")]
    assert source.closed == 1


@pytest.mark.asyncio
async def test_watchdog_absolute_total_deadline_is_not_reset_by_frequent_frames():
    async def frames():
        while True: await asyncio.sleep(.006); yield "frame"
    source=Source(frames()); terminal=[]
    result=await collect(TTSWatchdog(first_audio_seconds=.02,total_seconds=.025),source,terminal)
    assert result and terminal[0][0] == "tts-total-timeout" and source.closed == 1


@pytest.mark.asyncio
async def test_watchdog_provider_error_is_terminal_but_cancellation_propagates_after_cleanup():
    async def broken(): raise RuntimeError("provider detail"); yield
    source=Source(broken()); terminal=[]
    assert await collect(TTSWatchdog(.02,.1),source,terminal) == []
    assert terminal[0][0] == "tts-provider-error" and source.closed == 1
    async def blocked(): await asyncio.Event().wait(); yield
    blocked_source=Source(blocked()); task=asyncio.create_task(collect(TTSWatchdog(.2,.3),blocked_source,[]))
    await asyncio.sleep(0); task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert blocked_source.closed == 1


@pytest.mark.asyncio
async def test_terminal_closer_is_once_only_and_shutdown_survives_playout_failure():
    calls=[]
    class Handle:
        async def wait_for_playout(self): raise RuntimeError("playout")
    class Session:
        async def aclose(self): calls.append("aclose"); raise RuntimeError("close")
    ctx=SimpleNamespace(delete_room=lambda: delete(),shutdown=lambda **kw:calls.append(("shutdown",kw["reason"])))
    async def delete(): calls.append("delete")
    closer=TerminalCloser(ctx,Session(),grace_seconds=.02)
    handle=Handle()
    closer.trigger("stop",handle); closer.trigger("other",Handle()); await closer.aclose()
    assert calls == ["aclose","delete",("shutdown","stop")]


def test_terminal_trigger_without_running_loop_fails_safe_without_retaining_task():
    closer=TerminalCloser(SimpleNamespace(),SimpleNamespace())
    closer.trigger("stop")
    assert closer._task is None


@pytest.mark.asyncio
async def test_terminal_close_cancellation_still_reaches_shutdown_once():
    calls=[]
    class Handle:
        async def wait_for_playout(self): await asyncio.Event().wait()
    class Session:
        async def aclose(self): calls.append("aclose")
    async def delete(): calls.append("delete")
    closer=TerminalCloser(SimpleNamespace(delete_room=delete,shutdown=lambda **kw:calls.append("shutdown")),Session(),grace_seconds=.05)
    closer.trigger("stop",Handle()); await asyncio.sleep(0); closer._task.cancel(); await closer.aclose()
    assert calls == ["aclose","delete","shutdown"]


def test_reviewed_fallback_asset_is_bounded_pcm_and_enqueue_returns_exact_handle():
    import wave
    with wave.open(str(FALLBACK_AUDIO_PATH),"rb") as audio:
        assert audio.getnchannels() == 1 and audio.getsampwidth() == 2
        assert 0 < audio.getnframes()/audio.getframerate() <= 15
    assert FALLBACK_AUDIO_TRANSCRIPT_PATH.read_text().strip() == TTS_FALLBACK_ES
    handle=object(); calls=[]
    class Session:
        def say(self,text,**kwargs): calls.append((text,kwargs)); return handle
    returned=StaticFallbackAudioPlayer(Session()).enqueue()
    assert returned is handle
    assert calls[0][0] == "No he podido reproducir el audio. Terminamos por ahora."
    assert "audio" in calls[0][1]
