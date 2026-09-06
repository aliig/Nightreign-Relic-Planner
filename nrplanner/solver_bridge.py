"""Solver engine selection (and, from Phase 1, the Rust bridge).

The free-slot solver has two interchangeable implementations behind one seam
(``VesselOptimizer._solve_free_slots_*``).  ``NRPLANNER_SOLVER`` picks which:

    auto    (default) use the Rust extension when it imports, else Python
    rust    require the Rust extension; raise at import if it is missing
    python  always use the pure-Python solver

The switch exists only for the Rust migration: it is what lets the
differential parity test run both engines in one process.  It goes away with
the Python solver at switchover.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

VALID_ENGINES = ("auto", "rust", "python")

# The Rust extension module, or None when it is not installed.
try:  # pragma: no cover - depends on whether the wheel is built
    import nrplanner_core  # type: ignore
except ImportError:  # pragma: no cover
    nrplanner_core = None


def _resolve_default() -> str:
    """The engine every optimize() call uses unless it overrides it."""
    requested = os.environ.get("NRPLANNER_SOLVER", "auto").strip().lower()
    if requested not in VALID_ENGINES:
        raise ValueError(
            f"NRPLANNER_SOLVER={requested!r} is not one of {VALID_ENGINES}")
    if requested == "rust":
        if nrplanner_core is None:
            raise RuntimeError(
                "NRPLANNER_SOLVER=rust but the nrplanner_core extension is not "
                "installed — build it with "
                "`uv run maturin develop --release -m crates/nrplanner_core/Cargo.toml`"
            )
        return "rust"
    if requested == "python":
        return "python"
    return "rust" if nrplanner_core is not None else "python"


ENGINE: str = _resolve_default()

log.info("nrplanner solver engine=%s core=%s", ENGINE,
         "present" if nrplanner_core is not None else "absent")


def resolve_engine(engine: str | None) -> str:
    """Per-call engine override -> concrete engine name."""
    if engine is None:
        return ENGINE
    engine = engine.strip().lower()
    if engine not in VALID_ENGINES:
        raise ValueError(f"engine={engine!r} is not one of {VALID_ENGINES}")
    if engine == "auto":
        return ENGINE
    if engine == "rust" and nrplanner_core is None:
        raise RuntimeError(
            "engine='rust' requested but the nrplanner_core extension is not "
            "installed")
    return engine
