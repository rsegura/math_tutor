from types import SimpleNamespace
import inspect

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


def test_worker_startup_reconciles_orphans_before_recovering_deletions():
    events=[]
    service=SimpleNamespace(reconcile_startup=lambda:events.append("reconcile"),maintenance=lambda:events.append("maintenance"))
    runtime=worker.WorkerRetentionRuntime(SimpleNamespace(close=lambda:None),service,SimpleNamespace())
    runtime.startup_before_jobs()
    assert events == ["reconcile","maintenance"]


@pytest.mark.asyncio
async def test_consent_gate_durable_refresh_precedes_background_and_room_connect():
    events = []
    class Gate:
        async def refresh_once(self): events.append("durable-refresh")
        async def start(self): events.append("background-start")
    await worker.start_consent_gate_before_connect(Gate())
    events.append("room-connect")
    assert events == ["durable-refresh", "background-start", "room-connect"]


def test_entrypoint_awaits_consent_refresh_before_connecting_or_installing_frame_sink():
    source = inspect.getsource(worker.entrypoint)
    refresh = source.index("await start_consent_gate_before_connect(consent_gate)")
    connect = source.index("await ctx.connect()")
    frame_sink = source.index("LiveAudioFrameSink(")
    assert refresh < connect < frame_sink
