from types import SimpleNamespace

import pytest

from math_tutor.agent import worker


def test_worker_options_install_actual_prewarm_hook():
    options = worker.build_worker_options()
    assert options.prewarm_fnc is worker.prewarm_retention


def test_prewarm_sweeps_before_process_becomes_available(monkeypatch):
    events = []
    runtime = SimpleNamespace(startup_before_jobs=lambda: events.append("startup-sweep"))
    monkeypatch.setattr(worker.WorkerRetentionRuntime, "from_environment", classmethod(lambda cls, env: runtime))
    process = SimpleNamespace(userdata={})
    worker.prewarm_retention(process)
    events.append("ready")
    assert events == ["startup-sweep", "ready"]
    assert process.userdata[worker._RETENTION_RUNTIME_KEY] is runtime


@pytest.mark.asyncio
async def test_worker_runtime_owns_one_periodic_sweeper_and_awaits_it_before_repository_close():
    events = []
    class Sweeper:
        async def start(self): events.append("periodic-start")
        async def aclose(self): events.append("periodic-closed")
    class Repository:
        def close(self): events.append("repository-closed")
    runtime = worker.WorkerRetentionRuntime(Repository(), SimpleNamespace(sweep_expired=lambda: None), Sweeper())
    assert runtime.sweeper is runtime.sweeper
    await runtime.start_periodic()
    await runtime.start_periodic()
    await runtime.aclose()
    assert events == ["periodic-start", "periodic-closed", "repository-closed"]
