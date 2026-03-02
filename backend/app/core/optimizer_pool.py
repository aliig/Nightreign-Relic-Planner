"""ProcessPoolExecutor lifecycle for vessel optimization."""

import os
from concurrent.futures import ProcessPoolExecutor

from nrplanner.optimizer import init_optimizer_worker

_pool: ProcessPoolExecutor | None = None


def init_optimizer_pool(max_workers: int | None = None) -> None:
    global _pool
    if max_workers is None:
        max_workers = min(os.cpu_count() or 4, 8)
    _pool = ProcessPoolExecutor(
        max_workers=max_workers,
        initializer=init_optimizer_worker,
    )


def get_optimizer_pool() -> ProcessPoolExecutor | None:
    return _pool


def shutdown_optimizer_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=True, cancel_futures=True)
        _pool = None
