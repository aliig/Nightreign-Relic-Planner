"""Replay a debug-export bundle through the optimizer for local troubleshooting.

A bundle is produced by ``POST /api/v1/debug/export`` (the admin "Export debug"
button) and written to ``debug-exports/latest.json``.  This re-runs the optimizer
with the *current* code and game data on the captured build + inventory, prints
the top results, and flags any version drift / score difference vs what the app
recorded — turning "weird behavior" into a deterministic, inspectable repro.

Usage:
    python -m nrplanner.debug_replay debug-exports/latest.json
    python -m nrplanner.debug_replay debug-exports/latest.json --build <id> --profile <id>
"""
import argparse
import json
from pathlib import Path
from typing import Any

from nrplanner import BuildScorer, SourceDataHandler, VesselOptimizer
from nrplanner.constants import CHARACTER_NAMES
from nrplanner.models import BuildDefinition, OwnedRelic, RelicInventory
from nrplanner.optimizer import OPTIMIZER_VERSION

_RELIC_FIELDS = {
    "ga_handle", "item_id", "real_id", "color", "effects", "curses",
    "is_deep", "name", "tier",
}


def _pick_build(bundle: dict[str, Any], build_id: str | None) -> dict[str, Any] | None:
    builds = bundle.get("builds", [])
    wanted = build_id or (bundle.get("scenario") or {}).get("build_id")
    if wanted:
        for b in builds:
            if b.get("raw", {}).get("id") == wanted or b["build_def"]["id"] == wanted:
                return b
    return builds[0] if builds else None


def _pick_profile(bundle: dict[str, Any], profile_id: str | None) -> dict[str, Any] | None:
    profiles = bundle.get("profiles", [])
    wanted = profile_id or (bundle.get("scenario") or {}).get("profile_id")
    if wanted:
        for p in profiles:
            if p["id"] == wanted:
                return p
    return profiles[0] if profiles else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", help="path to a debug-export bundle (JSON)")
    ap.add_argument("--build", default=None, help="build id to replay (default: scenario/first)")
    ap.add_argument("--profile", default=None, help="profile id to use (default: scenario/first)")
    ap.add_argument("--top", type=int, default=5)
    args = ap.parse_args()

    bundle = json.loads(Path(args.path).read_text(encoding="utf-8"))
    meta = bundle.get("meta", {})
    print(f"# exported: {meta.get('exported_at')}  env={meta.get('environment')}  git={meta.get('git_sha')}")
    print(f"# game_data_version: {meta.get('game_data_version')}")
    print(
        f"# optimizer_version: bundle={meta.get('optimizer_version')} "
        f"current={OPTIMIZER_VERSION}"
        + ("  !! DRIFT (replay uses current code)"
           if meta.get("optimizer_version") != OPTIMIZER_VERSION else "")
    )

    b = _pick_build(bundle, args.build)
    p = _pick_profile(bundle, args.profile)
    if not b or not p:
        print("\nNo build/profile found in bundle.")
        return

    build = BuildDefinition(**b["build_def"])
    relics = [
        OwnedRelic(**{k: v for k, v in r.items() if k in _RELIC_FIELDS})
        for r in p["relics"]
    ]
    inventory = RelicInventory.from_owned_relics(relics)
    print(
        f"\nBuild: {build.name} ({build.character})  |  "
        f"Profile: {p['name']} ({len(relics)} relics)\n"
    )

    try:
        hero_type = list(CHARACTER_NAMES).index(build.character) + 1
    except ValueError:
        print(f"Unknown character {build.character!r}; cannot map to hero type.")
        return

    ds = SourceDataHandler(language="en_US")
    optimizer = VesselOptimizer(ds, BuildScorer(ds))
    results = optimizer.optimize_all_vessels(build, inventory, hero_type, top_n=args.top)
    print(f"Top {len(results)} arrangements (replayed with current code):")
    for i, r in enumerate(results, start=1):
        flag = "" if r.meets_requirements else "  (missing requirements)"
        print(f"  {i}. {r.vessel_name}: {r.total_score} pts{flag}")

    scenario_results = (bundle.get("scenario") or {}).get("results")
    if scenario_results:
        then = scenario_results[0].get("total_score")
        now = results[0].total_score if results else None
        verdict = "MATCH" if then == now else "DIFF — investigate"
        print(f"\nTop score: app-recorded={then}  replay={now}  ->  {verdict}")


if __name__ == "__main__":
    main()
