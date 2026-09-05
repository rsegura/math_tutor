"""Bounded speech timeout and once-only terminal close helpers."""

from __future__ import annotations

import asyncio


class TTSWatchdog:
    def __init__(self, timeout_seconds: float = 10.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds

    async def wait(self, awaitable):
        async with asyncio.timeout(self.timeout_seconds):
            return await awaitable

    async def iterate(self, source):
        iterator = source.__aiter__()
        while True:
            try:
                yield await self.wait(anext(iterator))
            except StopAsyncIteration:
                return


class TerminalCloser:
    def __init__(self, ctx, session, *, grace_seconds: float = 10.0) -> None:
        self.ctx, self.session, self.grace_seconds = ctx, session, grace_seconds
        self._task = None

    def trigger(self, reason: str) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._close(reason))

    async def _close(self, reason: str) -> None:
        try:
            handle = self.session.current_speech
            if handle is not None:
                async with asyncio.timeout(self.grace_seconds):
                    await handle.wait_for_playout()
        except Exception:
            pass
        finally:
            await self.session.aclose()
            try:
                await self.ctx.delete_room()
            finally:
                self.ctx.shutdown(reason=reason)

    async def aclose(self) -> None:
        if self._task is not None and self._task is not asyncio.current_task():
            await self._task
