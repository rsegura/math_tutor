import asyncio
from datetime import datetime, timezone

import pytest

import time

from math_tutor.infrastructure.clip_retention import AsyncConsentGate, RetentionSettings, RetentionSweeper


def test_retention_settings_are_default_off_and_strictly_bounded():
    assert RetentionSettings.from_environment({}).enabled is False
    with pytest.raises(ValueError):
        RetentionSettings.from_environment({"AUDIO_EVIDENCE_ENABLED": "true", "AUDIO_EVIDENCE_RETENTION_DAYS": "31"})
    with pytest.raises(ValueError):
        RetentionSettings(enabled=True, retention_days=31)


@pytest.mark.asyncio
async def test_periodic_sweeper_never_overlaps_and_shutdown_is_awaited():
    active = 0
    maximum = 0

    async def sweep():
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.02)
        active -= 1

    worker = RetentionSweeper(sweep, interval_seconds=60)
    await asyncio.gather(worker.sweep_once(), worker.sweep_once())
    await worker.start()
    await worker.aclose()
    assert maximum == 1
    assert active == 0


@pytest.mark.asyncio
async def test_consent_refresh_is_off_thread_and_revocation_invalidates_cache():
    active = [True]
    revoked = asyncio.Event()
    def slow_check():
        time.sleep(.05)
        return active[0]
    gate = AsyncConsentGate(slow_check, initially_enabled=True, refresh_seconds=.02, on_revoked=revoked.set)
    await gate.start()
    ticks = 0
    async def ticker():
        nonlocal ticks
        for _ in range(20):
            await asyncio.sleep(.005); ticks += 1
    active[0] = False
    await asyncio.gather(ticker(), asyncio.wait_for(revoked.wait(), .3))
    assert ticks == 20
    assert gate.allows_capture() is False
    await gate.aclose()


@pytest.mark.asyncio
async def test_bootstrap_true_does_not_authorize_until_initial_durable_refresh():
    gate = AsyncConsentGate(lambda: False, initially_enabled=True, refresh_seconds=.02)
    assert gate.allows_capture() is False
    await gate.refresh_once()
    assert gate.allows_capture() is False
