"""Benchmark the vessel optimizer end to end.

Standalone CLI over the nrplanner library — no database, no backend, no
fixtures.  Builds a synthetic inventory of legal relics plus a set of random
builds (nrplanner.tests._solver_scenarios), then times:

  * per vessel: prep / solve / result-building milliseconds, nodes, truncation
  * per build:  wall time and the single vessel on its critical path
  * end to end: sequential and pooled wall time over every build

Its reason for existing is before/after comparison across the Rust solver
migration: write a JSON baseline with the Python engine, then re-run with the
Rust one and diff the same fields.

Run:
    uv run python scripts/bench_solver.py --relics 2000 --builds 64 \\
        --seed 7 --engine python --out baseline.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

from nrplanner import solver_bridge
from nrplanner.data import SourceDataHandler
from nrplanner.models import RelicInventory
from nrplanner.optimizer import (
    DEFAULT_BACKTRACK_DEADLINE_SECS, OPTIMIZER_VERSION, VesselOptimizer,
    init_optimizer_worker,
)
from nrplanner.scoring import BuildScorer
from nrplanner.tests import _solver_scenarios as scenarios


# ---------------------------------------------------------------------------
# Workload
# ---------------------------------------------------------------------------

def build_workload(ds: SourceDataHandler, n_relics: int, n_builds: int,
                   seed: int) -> tuple[RelicInventory, list[scenarios.Scenario]]:
    """One shared inventory of legal relics + ``n_builds`` random builds.

    Every build solves against the SAME inventory, which is what the app does
    (one save, many builds) and what makes the per-build numbers comparable.
    """
    t0 = time.perf_counter()
    relics = scenarios.legal_relics(ds, seed, n_relics)
    gen_s = time.perf_counter() - t0
    inventory = RelicInventory.from_owned_relics(relics)

    # One build generator run over the shared relic list: the builds' effect
    # weights are drawn from the ids those relics actually carry, which is what
    # makes the solver do real work.
    out: list[scenarios.Scenario] = []
    for i, sc in enumerate(scenarios.legal_scenarios(
            ds, seed, n=n_builds, relics=relics)):
        sc.name = f"build-{i}"
        sc.inventory = inventory
        out.append(sc)
    print(f"workload: {len(relics)} relics ({gen_s:.1f}s to roll), "
          f"{len(out)} builds")
    return inventory, out


# ---------------------------------------------------------------------------
# Per-vessel timing
# ---------------------------------------------------------------------------

def time_one_build(optimizer: VesselOptimizer, sc: scenarios.Scenario,
                   ds: SourceDataHandler, engine: str, top_n: int,
                   deadline_secs: float) -> dict:
    """Solve every vessel of one build sequentially, timing each."""
    vessels = ds.get_all_vessels_for_hero(sc.hero_type)
    per_vessel: list[dict] = []
    t_build = time.perf_counter()
    for v in vessels:
        vd = dict(v)
        vd["_id"] = v["vessel_id"]
        t0 = time.perf_counter()
        results = optimizer.optimize(sc.build, sc.inventory, vd, top_n,
                                     deadline_secs=deadline_secs,
                                     engine=engine)
        total_ms = (time.perf_counter() - t0) * 1000.0
        stats = optimizer.last_solve_stats
        solve_ms = stats.get("solve_ms", 0.0)
        per_vessel.append({
            "vessel_id": v["vessel_id"],
            "name": v["Name"],
            "total_ms": total_ms,
            "solve_ms": solve_ms,
            # Everything outside the seam: candidate pre-filtering happens
            # inside solve_ms, so this is result building + post-hoc filters.
            "result_ms": total_ms - solve_ms,
            "nodes": stats.get("nodes", 0),
            "truncated": stats.get("truncated", False),
            "candidates": stats.get("candidates", []),
            "results": len(results),
        })
    wall_ms = (time.perf_counter() - t_build) * 1000.0
    critical = max(per_vessel, key=lambda p: p["solve_ms"], default=None)
    return {
        "build": sc.name,
        "character": sc.build.character,
        "hero_type": sc.hero_type,
        "include_deep": sc.build.include_deep,
        "wall_ms": wall_ms,
        "vessels": per_vessel,
        "critical_vessel": None if critical is None else {
            "name": critical["name"],
            "solve_ms": critical["solve_ms"],
            "nodes": critical["nodes"],
        },
    }


# ---------------------------------------------------------------------------
# Pooled end-to-end
# ---------------------------------------------------------------------------

def _make_pool(kind: str, workers: int):
    if kind == "process":
        return ProcessPoolExecutor(max_workers=workers,
                                   initializer=init_optimizer_worker)
    if kind == "thread":
        return ThreadPoolExecutor(max_workers=workers,
                                  thread_name_prefix="nr-bench")
    raise ValueError(f"unknown pool kind {kind!r}")


def run_pooled(ds: SourceDataHandler, scs: list[scenarios.Scenario],
               kind: str, workers: int, top_n: int, max_per_vessel: int,
               deadline_secs: float) -> dict:
    """All builds through a pool, with the app's cross-build prefetch depth.

    Mirrors ``optimizer_pool.prefetch_depth`` (3x the pool width): the next
    builds' vessels are submitted before the current build's futures are
    drained, so the pool's tail never starves.
    """
    optimizer = VesselOptimizer(ds, BuildScorer(ds))
    depth = max(4, workers * 3)
    t0 = time.perf_counter()
    with _make_pool(kind, workers) as pool:
        inflight: list[tuple[scenarios.Scenario, dict]] = []
        pending = list(scs)
        n_results = 0
        while pending or inflight:
            while pending and len(inflight) < depth:
                sc = pending.pop(0)
                inflight.append((sc, optimizer.submit_all_vessels(
                    sc.build, sc.inventory, sc.hero_type, max_per_vessel,
                    pool, deadline_secs)))
            sc, futures = inflight.pop(0)
            n_results += len(optimizer.collect_all_vessels(
                sc.build, sc.hero_type, futures, top_n,
                n_relics=len(sc.inventory)))
    wall_ms = (time.perf_counter() - t0) * 1000.0
    return {"pool": kind, "workers": workers, "prefetch_depth": depth,
            "wall_ms": wall_ms, "results": n_results}


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _summarize(builds: list[dict]) -> dict:
    solve_ms = [v["solve_ms"] for b in builds for v in b["vessels"]]
    walls = [b["wall_ms"] for b in builds]
    return {
        "builds": len(builds),
        "vessels": len(solve_ms),
        "sequential_wall_ms": sum(walls),
        "build_wall_ms_mean": statistics.mean(walls) if walls else 0.0,
        "build_wall_ms_max": max(walls, default=0.0),
        "vessel_solve_ms_mean": statistics.mean(solve_ms) if solve_ms else 0.0,
        "vessel_solve_ms_max": max(solve_ms, default=0.0),
        "vessel_solve_ms_total": sum(solve_ms),
        "nodes_total": sum(v["nodes"] for b in builds for v in b["vessels"]),
        "truncated": sum(1 for b in builds for v in b["vessels"]
                         if v["truncated"]),
    }


def _print_report(report: dict) -> None:
    s = report["summary"]
    print(f"\nengine={report['engine']}  optimizer_version={OPTIMIZER_VERSION}")
    print(f"  builds={s['builds']} vessels={s['vessels']} "
          f"nodes={s['nodes_total']} truncated={s['truncated']}")
    print(f"  sequential wall  {s['sequential_wall_ms']:9.0f} ms")
    print(f"  solve total      {s['vessel_solve_ms_total']:9.0f} ms "
          f"({100 * s['vessel_solve_ms_total'] / max(s['sequential_wall_ms'], 1):.0f}% of wall)")
    print(f"  vessel solve     mean {s['vessel_solve_ms_mean']:.1f} ms  "
          f"max {s['vessel_solve_ms_max']:.1f} ms")
    worst = sorted(
        (v for b in report["builds"] for v in b["vessels"]),
        key=lambda v: v["solve_ms"], reverse=True)[:10]
    print("  slowest vessels:")
    for v in worst:
        print(f"    {v['solve_ms']:8.1f} ms  nodes={v['nodes']:<9d} {v['name']}")
    if report.get("pooled"):
        p = report["pooled"]
        print(f"  pooled ({p['pool']}, workers={p['workers']}, "
              f"depth={p['prefetch_depth']}): {p['wall_ms']:.0f} ms")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--relics", type=int, default=2000)
    ap.add_argument("--builds", type=int, default=64)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--engine", choices=("auto", "rust", "python"),
                    default="auto")
    ap.add_argument("--pool", choices=("none", "process", "thread"),
                    default="none",
                    help="also time an end-to-end pooled run")
    ap.add_argument("--workers", type=int, default=0,
                    help="pool width (0 = os.process_cpu_count())")
    ap.add_argument("--top-n", type=int, default=10)
    ap.add_argument("--max-per-vessel", type=int, default=3)
    ap.add_argument("--deadline", type=float,
                    default=DEFAULT_BACKTRACK_DEADLINE_SECS)
    ap.add_argument("--out", type=str, default=None,
                    help="write the full report as JSON here")
    args = ap.parse_args()

    engine = solver_bridge.resolve_engine(args.engine)
    ds = SourceDataHandler(language="en_US")
    _inventory, scs = build_workload(ds, args.relics, args.builds, args.seed)

    optimizer = VesselOptimizer(ds, BuildScorer(ds))
    builds: list[dict] = []
    for i, sc in enumerate(scs, 1):
        b = time_one_build(optimizer, sc, ds, engine, args.max_per_vessel,
                           args.deadline)
        builds.append(b)
        print(f"  [{i}/{len(scs)}] {sc.name} {sc.build.character:<10} "
              f"{b['wall_ms']:8.0f} ms")

    report = {
        "engine": engine,
        "optimizer_version": OPTIMIZER_VERSION,
        "args": vars(args),
        "summary": _summarize(builds),
        "builds": builds,
    }

    if args.pool != "none":
        workers = args.workers or (getattr(os, "process_cpu_count", None)
                                   or os.cpu_count)() or 1
        report["pooled"] = run_pooled(
            ds, scs, args.pool, workers, args.top_n, args.max_per_vessel,
            args.deadline)

    _print_report(report)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
