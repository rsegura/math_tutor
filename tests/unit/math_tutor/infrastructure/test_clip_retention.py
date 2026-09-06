import asyncio
from datetime import datetime, timezone

import pytest

from math_tutor.infrastructure.clip_retention import RetentionSettings, RetentionSweeper


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
