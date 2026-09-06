import pytest

from math_tutor.agent.worker import RetentionLifecycle


@pytest.mark.asyncio
async def test_startup_sweep_finishes_before_worker_is_ready_and_teardown_closes_it():
    events = []

    class Sweeper:
        async def sweep_once(self): events.append("swept")
        async def start(self): events.append("started")
        async def aclose(self): events.append("closed")

    lifecycle = RetentionLifecycle(Sweeper())
    await lifecycle.start_before_jobs()
    events.append("ready")
    await lifecycle.aclose()
    assert events == ["swept", "started", "ready", "closed"]
