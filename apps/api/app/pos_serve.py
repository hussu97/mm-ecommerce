"""Serve the register API under uvicorn's process supervisor, even with one worker.

**Why not plain `uvicorn app.pos_main:app`.** With `--workers 1` uvicorn serves
in-process, with nothing above the worker. The register's postings replay a
warehouse's FIFO ledger in-process (`costing_service`), and each replay leaves
the process's RSS higher. The allocator never gives it back
(`app.core.memory_telemetry`), so over days the worker sat at its 384 MiB cap
and swapped. The fix is to replace the worker now and then. In-process, that
means the till sees the port go away.

Under `uvicorn.supervisors.Multiprocess` it doesn't. On SIGHUP the supervisor
starts a fresh worker on the same listening socket, waits until it is serving,
and only then drains the old one. The worker itself sends that SIGHUP once its
memory stays over the ceiling (`pos_main`'s lifespan, keyed on
`SUPERVISED_ENV`). The supervisor imports none of the app: it plus
multiprocessing's resource tracker cost ~39 MB resident on Linux, which is why
`pos_main.RECYCLE_ABOVE_MB` leaves room under the 384 MiB cap. During the
handover the two workers' DB pools overlap for seconds (lazy, old one
draining), inside the spare slots `test_a_live_generation_fits_under_max_connections`
keeps for exactly this. It also replaces a worker that dies, and on SIGTERM
drains every worker, so `exec` this as the container's PID 1.
"""

from __future__ import annotations

import argparse
import os

import uvicorn
from uvicorn.supervisors import Multiprocess

#: Set for the workers this supervisor spawns; a worker only asks for a
#: replacement when it has a supervisor to ask.
SUPERVISED_ENV = "MM_UVICORN_SUPERVISED"

#: How long the supervisor waits on a worker: for a new one to come up, and
#: for a busy one to answer its liveness ping before it is killed. Uvicorn's
#: 5 s default would kill a worker in the middle of a long, CPU-bound
#: replay (16-45 s under swap), which plain uvicorn never did.
WORKER_HEALTHCHECK_TIMEOUT_S = 60


def config(workers: int) -> uvicorn.Config:
    return uvicorn.Config(
        "app.pos_main:app",
        host="0.0.0.0",  # noqa: S104 — inside the container network, behind nginx
        port=8000,
        proxy_headers=True,
        forwarded_allow_ips="*",
        workers=workers,
        log_level="warning",
        timeout_graceful_shutdown=8,
        timeout_keep_alive=15,
        timeout_worker_healthcheck=WORKER_HEALTHCHECK_TIMEOUT_S,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    os.environ[SUPERVISED_ENV] = "1"  # inherited by the spawned workers
    cfg = config(max(1, args.workers))
    Multiprocess(cfg, sockets=[cfg.bind_socket()]).run()


if __name__ == "__main__":
    main()
