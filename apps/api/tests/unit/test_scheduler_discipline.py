"""
Structural guards for the WP5 scheduler discipline.

Full pool behaviour under load is not unit-testable — you cannot provoke the
race that stranded a connection in a unit test, which is why the original bug
reached production. What IS checkable is the shape: that every loop is
leader-elected, beats a heartbeat, and draws its locks from the scheduler pool.
These assert that shape so a future loop cannot quietly drop one of the
guarantees.
"""

from __future__ import annotations

import inspect

from app.core import advisory_lock, database, heartbeat


def test_advisory_lock_binds_to_the_scheduler_engine():
    # F-OPS-13: the leader lock (checked out for the whole life of leadership)
    # must live on the scheduler pool, never a request-pool connection.
    assert advisory_lock.engine is database.scheduler_engine


def test_the_source_event_sweeper_is_leader_elected_and_disciplined():
    from app.services.inventory import source_event_service as ses

    src = inspect.getsource(ses.run_sweeper_forever)
    # Leader election it did not have before (F-OPS-2).
    assert "advisory_lock.held" in src
    # A per-tick budget so a wedged branch cannot pin the leader connection.
    assert "asyncio.timeout" in src
    # Sleeps first (recovery work, not urgent) and beats.
    assert "asyncio.sleep" in src
    assert 'heartbeat.beat("inventory_source_event_sweeper")' in src

    # The per-branch lock is now non-blocking with a lock_timeout (was a blocking
    # xact lock with none), and one session per branch.
    try_lock = inspect.getsource(ses._try_lock_branch_inventory)
    assert "pg_try_advisory_xact_lock" in try_lock
    assert "lock_timeout" in try_lock
    sweep = inspect.getsource(ses.sweep_pending_once)
    assert "SchedulerSessionFactory" in sweep


#: Loop module → the `run_forever`/loop function whose source must beat, and the
#: heartbeat name it must beat. Names must match `heartbeat.LOOP_NAMES` and the
#: `spawn_tracked(..., name=...)` calls in `app_setup`.
_LOOP_BEATS = {
    "app.services.delivery.delivery_scheduler": (
        "run_forever",
        "delivery_scheduler",
    ),
    "app.services.log_retention": ("run_forever", "log_retention"),
    "app.services.inventory.source_event_service": (
        "run_sweeper_forever",
        "inventory_source_event_sweeper",
    ),
    "app.services.pos.daily_sales_email": ("run_forever", "daily_sales_email"),
    "app.services.branch_hours_sync": ("run_forever", "branch_hours_sync"),
    "app.services.grubops.grubops_reconcile": ("run_forever", "grubops_reconcile"),
    "app.services.grubops.grubops_orders": ("run_forever", "grubops_orders"),
}


def test_every_loop_beats_its_registered_heartbeat():
    import importlib

    for module_path, (func_name, beat_name) in _LOOP_BEATS.items():
        module = importlib.import_module(module_path)
        src = inspect.getsource(getattr(module, func_name))
        assert f'heartbeat.beat("{beat_name}")' in src, (
            f"{module_path}.{func_name} does not beat {beat_name!r}"
        )
        assert beat_name in heartbeat.LOOP_NAMES, (
            f"{beat_name!r} is not declared in heartbeat.LOOP_NAMES"
        )


def test_the_aggregator_supervisor_beats_while_leading():
    from app.services.aggregators import ingest

    # A dedicated beater child, because the supervisor blocks in `gather` for the
    # whole leadership tenure and the daily/rolling children tick too rarely.
    src = inspect.getsource(ingest._leader_heartbeat_forever)
    assert 'heartbeat.beat("aggregator_ingest")' in src
    assert "aggregator_ingest" in heartbeat.LOOP_NAMES


def test_heartbeat_names_match_the_spawned_loop_names():
    # The names `/health` reports must be exactly the names the lifespan spawns.
    from app import app_setup

    lifespan_src = inspect.getsource(app_setup.make_lifespan)
    for name in heartbeat.LOOP_NAMES:
        assert f'name="{name}"' in lifespan_src, (
            f"{name!r} is in heartbeat.LOOP_NAMES but no loop is spawned under it"
        )
