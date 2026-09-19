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
