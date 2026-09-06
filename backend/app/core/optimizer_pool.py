"""Thread-pool lifecycle for vessel optimization.

Threads, not processes: the Rust solver releases the GIL for the whole search,
so the vessels of one build run genuinely in parallel while sharing one
compiled inventory by reference.  The process pool this replaced had to pickle
the entire relic inventory into every vessel task and recompile each relic
profile in the worker, which dominated the wall clock once the solver itself
got fast.
"""

import os
from concurrent.futures import ThreadPoolExecutor

from nrplanner.optimizer import warm_data_source

from app.core.config import settings
from app.core.game_data import get_game_data

_pool: ThreadPoolExecutor | None = None
_width: int = 0


def _available_cpus() -> int:
    """CPUs this process may actually use.

    ``os.cpu_count()`` reports the machine's CPUs, which over-reports inside a
    container with a cgroup quota or under an affinity mask —
    ``os.process_cpu_count()`` (3.13+) honours both.
    """
    getter = getattr(os, "process_cpu_count", None)
    if getter is not None:
        return getter() or 1
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0)) or 1
    return os.cpu_count() or 1


def resolve_max_workers() -> int:
    """Pool width: the configured override, else all available CPUs (capped)."""
    if settings.OPTIMIZER_MAX_WORKERS > 0:
        return settings.OPTIMIZER_MAX_WORKERS
    return max(1, min(_available_cpus(), settings.OPTIMIZER_MAX_WORKERS_CAP))


def init_optimizer_pool(max_workers: int | None = None) -> None:
    global _pool, _width
    if max_workers is None:
        max_workers = resolve_max_workers()
    _width = max_workers
    # The data source's lazy caches are built non-atomically, so they must be
    # warmed while only this thread is running — before any task can race.
    warm_data_source(get_game_data())
    _pool = ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="nr-solve",
    )


def get_optimizer_pool() -> ThreadPoolExecutor | None:
    return _pool


def get_pool_width() -> int:
    """Worker count of the live pool (0 when no pool is running)."""
    return _width


def prefetch_depth() -> int:
    """How many builds may have vessel tasks in flight simultaneously.

    Defaults to 3x the pool width: at depth == width the pool still starves,
    because most vessels solve in single-digit milliseconds and only one or
    two per build carry real work (see OPTIMIZER_PREFETCH_BUILDS).
    """
    if settings.OPTIMIZER_PREFETCH_BUILDS > 0:
        return settings.OPTIMIZER_PREFETCH_BUILDS
    return max(4, (_width or resolve_max_workers()) * 3)


def shutdown_optimizer_pool() -> None:
    global _pool, _width
    if _pool is not None:
        _pool.shutdown(wait=True, cancel_futures=True)
        _pool = None
    _width = 0
