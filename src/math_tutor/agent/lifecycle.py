"""Bounded speech timeout and once-only terminal close helpers."""

from __future__ import annotations

import asyncio
import inspect
from array import array

TTS_FALLBACK_ES = "No he podido reproducir el audio. Terminamos por ahora."


class StaticFallbackAudioPlayer:
    """Play a bounded provider-independent alert directly as RTC PCM frames."""
    def __init__(self, session) -> None:
        self.session = session
        self._played = False

    async def play(self) -> None:
        if self._played:
            return
        self._played = True
        from livekit import rtc

        async def frames():
            sample_rate, frame_samples = 24_000, 2_400
            for amplitude in (2_500, 0, 2_500):
                pcm = array("h", (amplitude if (index // 60) % 2 == 0 else -amplitude for index in range(frame_samples)))
                yield rtc.AudioFrame(data=pcm.tobytes(), sample_rate=sample_rate, num_channels=1, samples_per_channel=frame_samples)
                await asyncio.sleep(0)

        handle = self.session.say(TTS_FALLBACK_ES, audio=frames(), allow_interruptions=False)
        if inspect.isawaitable(handle):
            try:
                async with asyncio.timeout(1.0):
                    await handle
            except (Exception, asyncio.CancelledError):
                return


async def _bounded_close(source, seconds: float = 1.0) -> None:
    close = getattr(source, "aclose", None)
    if close is None:
        return
    try:
        async with asyncio.timeout(seconds):
            await asyncio.shield(close())
    except (Exception, asyncio.CancelledError):
        return


async def _bounded_step(awaitable, seconds: float) -> None:
    """Finish or cancel one teardown step even if the closer is cancelled."""
    task = asyncio.create_task(awaitable)
    try:
        async with asyncio.timeout(seconds):
            await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            async with asyncio.timeout(seconds):
                await asyncio.shield(task)
        except (Exception, asyncio.CancelledError):
            task.cancel()
    except Exception:
        task.cancel()
    finally:
        if not task.done():
            task.cancel()
        try:
            await task
        except (Exception, asyncio.CancelledError):
            pass


class TTSWatchdog:
    """First-frame and absolute-total deadlines; neither is reset per frame."""
    def __init__(self, first_audio_seconds: float = 4.0, total_seconds: float = 15.0) -> None:
        if first_audio_seconds <= 0 or total_seconds <= 0 or first_audio_seconds > total_seconds:
            raise ValueError("TTS deadlines must be positive and first <= total")
        self.first_audio_seconds = first_audio_seconds
        self.total_seconds = total_seconds

    async def _terminal(self, callback, reason: str) -> None:
        if callback is None:
            return
        result = callback(reason, TTS_FALLBACK_ES)
        if inspect.isawaitable(result):
            await result

    async def iterate(self, source, *, on_terminal=None):
        iterator = source.__aiter__()
        loop = asyncio.get_running_loop()
        total_deadline = loop.time() + self.total_seconds
        first = True
        try:
            while True:
                remaining_total = total_deadline - loop.time()
                if remaining_total <= 0:
                    await _bounded_close(source)
                    await self._terminal(on_terminal, "tts-total-timeout")
                    return
                wait = min(self.first_audio_seconds, remaining_total) if first else remaining_total
                try:
                    async with asyncio.timeout(wait):
                        frame = await anext(iterator)
                except StopAsyncIteration:
                    return
                except TimeoutError:
                    reason = "tts-first-audio-timeout" if first else "tts-total-timeout"
                    await _bounded_close(source)
                    await self._terminal(on_terminal, reason)
                    return
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await _bounded_close(source)
                    await self._terminal(on_terminal, "tts-provider-error")
                    return
                first = False
                yield frame
        except (GeneratorExit, asyncio.CancelledError):
            await _bounded_close(source)
            raise


class TerminalCloser:
    """Drain speech and close the call once; teardown failures never skip later steps."""
    def __init__(self, ctx, session, *, grace_seconds: float = 10.0) -> None:
        self.ctx, self.session, self.grace_seconds = ctx, session, grace_seconds
        self._task: asyncio.Task | None = None

    def trigger(self, reason: str) -> None:
        if self._task is not None:
            return
        coroutine = self._close(reason)
        try:
            self._task = asyncio.create_task(coroutine)
        except RuntimeError:
            coroutine.close()
            self._task = None

    async def _close(self, reason: str) -> None:
        try:
            async with asyncio.timeout(self.grace_seconds):
                handle = self.session.current_speech
                if handle is None:
                    await asyncio.sleep(0.05)
                    handle = self.session.current_speech
                while handle is not None:
                    await handle.wait_for_playout()
                    await asyncio.sleep(0)
                    following = self.session.current_speech
                    if following is handle:
                        await asyncio.sleep(0.05)
                        following = self.session.current_speech
                        if following is handle:
                            break
                    handle = following
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        await _bounded_step(self.session.aclose(), min(4.0, self.grace_seconds))
        try:
            await _bounded_step(self.ctx.delete_room(), min(4.0, self.grace_seconds))
        finally:
            self.ctx.shutdown(reason=reason)

    async def aclose(self) -> None:
        task = self._task
        if task is None or task is asyncio.current_task():
            return
        try:
            await task
        except asyncio.CancelledError:
            return
