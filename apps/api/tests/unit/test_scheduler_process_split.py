"""
The storefront `api` container runs the schedulers in a sibling process.

Running them in the HTTP worker's event loop let a heavy aggregator sweep starve
checkout and, under pool exhaustion, drop an order's commit on a reaped
connection (MM-20260919-007). The start script splits the container into two
processes; these assert the wiring that makes that split correct and safe, since
there is no runtime handle on "the container ran two processes".
"""

from __future__ import annotations

import inspect
import pathlib

SCRIPT = (
    pathlib.Path(__file__).parents[2] / "scripts" / "start-web-and-scheduler.sh"
).read_text()


def test_the_two_children_are_the_web_worker_and_the_scheduler():
    assert "uvicorn app.main:app" in SCRIPT
    assert "python -m app.scheduler_runner" in SCRIPT


def test_only_the_scheduler_child_runs_the_loops():
    # Decided per child, not by the shared container env: the web worker gets the
    # flag off (so app.main's lifespan starts none) and the scheduler gets it on.
    assert "STOREFRONT_SCHEDULER_ENABLED=false uvicorn app.main:app" in SCRIPT
    assert "STOREFRONT_SCHEDULER_ENABLED=true python -m app.scheduler_runner" in SCRIPT


def test_a_dead_child_brings_the_container_down():
    # A dead scheduler must not hide behind a still-healthy HTTP port: the script
    # waits for the first child to exit and then exits non-zero so Docker
    # recreates the container.
    assert "wait -n" in SCRIPT
    assert 'exit "${code}"' in SCRIPT


def test_scheduler_runner_refuses_to_run_with_the_flag_off():
    # If the flag were off in the scheduler process, the loops would silently
    # never start; the runner exits non-zero instead so the container restarts.
    from app import scheduler_runner

    src = inspect.getsource(scheduler_runner._run)
    assert "STOREFRONT_SCHEDULER_ENABLED" in src
    assert "raise SystemExit(1)" in src


def test_a_recycling_scheduler_is_relaunched_alone():
    # Status 75 from the scheduler is a planned memory recycle: only it is
    # relaunched and the web worker keeps serving. Any other exit, or any exit
    # while the container is stopping, still takes the container down.
    from app import scheduler_runner

    assert scheduler_runner.RECYCLE_EXIT_CODE == 75
    assert "RECYCLE_EXIT_CODE=75" in SCRIPT
    assert "wait -n -p exited" in SCRIPT
    assert '"${exited}" == "${sched_pid}"' in SCRIPT
    assert '-z "${stopping}"' in SCRIPT
    src = inspect.getsource(scheduler_runner._run)
    assert "raise SystemExit(RECYCLE_EXIT_CODE)" in src
    assert "recycle_above_mb=RECYCLE_ABOVE_MB" in src


def test_the_runner_drains_and_exits_75_when_telemetry_asks_to_recycle(monkeypatch):
    import asyncio

    import pytest

    from app import scheduler_runner
    from app.core import memory_telemetry

    stopped: list[str] = []

    async def start():
        return []

    async def stop(tasks):
        stopped.append("loops")

    async def recycle_now(role, *, recycle_above_mb=None, on_recycle=None):
        assert recycle_above_mb == scheduler_runner.RECYCLE_ABOVE_MB
        on_recycle()

    monkeypatch.setattr(scheduler_runner.settings, "STOREFRONT_SCHEDULER_ENABLED", True)
    monkeypatch.setattr(scheduler_runner, "configure_observability", lambda **_: None)
    monkeypatch.setattr(scheduler_runner, "start_storefront_schedulers", start)
    monkeypatch.setattr(scheduler_runner, "stop_storefront_schedulers", stop)
    monkeypatch.setattr(memory_telemetry, "run_forever", recycle_now)

    with pytest.raises(SystemExit) as exc:
        asyncio.run(scheduler_runner._run())
    assert exc.value.code == scheduler_runner.RECYCLE_EXIT_CODE
    assert stopped == ["loops"]  # the same graceful drain a deploy does
