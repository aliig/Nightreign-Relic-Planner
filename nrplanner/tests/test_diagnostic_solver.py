"""Diagnostic: compare backtrack vs greedy vs hybrid solver for the user's Guardian build.

Run with:  uv run pytest nrplanner/tests/test_diagnostic_solver.py -v -s
"""
import json
import tempfile
from pathlib import Path

import pytest

from nrplanner import (
    BuildScorer,
    SourceDataHandler,
    VesselOptimizer,
    decrypt_sl2,
    discover_characters,
    parse_relics,
)
from nrplanner.models import (
    BuildDefinition,
    RelicInventory,
    WeightGroup,
)

FIXTURE_PATH = (
    Path(__file__).parent.parent.parent
    / "backend" / "tests" / "fixtures" / "NR0000.sl2"
)

# Effect IDs from effects.json
_GUARDIAN_CHAR_SKILL = 6500100   # [Guardian] Character Skill Boosts Damage Negation
_GUARDIAN_STR_DEX    = 6641000   # [Guardian] Improved Strength and Dexterity, Reduced Vigor
_GUARDIAN_HP_RESTORE = 7012000   # [Guardian] Slowly restores nearby allies' HP while Art is active
_GUARDIAN_SHOCKWAVE  = 7033400   # [Guardian] Successful guards send out shockwaves
_DAMAGE_NEG_LOW_HP  = 340800    # Improved Damage Negation at Low HP
# NOTE: 6001400's FMG entry says "+4", but attachTextId→7001403 resolves to "+3".
_PHYS_ATK_UP_3      = 6001400   # Physical Attack Up +3 (game engine name)
_VIGOR_3            = 7000002   # Vigor +3


def _make_guardian_build() -> BuildDefinition:
    """Build matching the user's screenshot."""
    return BuildDefinition(
        id="guardian-test",
        name="test (Guardian)",
        character="Guardian",
        groups=[
            WeightGroup(
                weight=10,
                effects=[
                    _GUARDIAN_CHAR_SKILL,
                    _GUARDIAN_STR_DEX,
                    _GUARDIAN_HP_RESTORE,
                    _GUARDIAN_SHOCKWAVE,
                ],
            ),
            WeightGroup(
                weight=5,
                effects=[_DAMAGE_NEG_LOW_HP, _PHYS_ATK_UP_3],
            ),
            WeightGroup(
                weight=1,
                effects=[_VIGOR_3],
            ),
        ],
        required_effects=[],
        excluded_effects=[],
        excluded_stacking_categories=[300, 6630000],
        include_deep=True,
        curse_max=1,
    )


@pytest.fixture(scope="module")
def ds() -> SourceDataHandler:
    return SourceDataHandler(language="en_US")


@pytest.fixture(scope="module")
def real_inventory(ds: SourceDataHandler) -> RelicInventory:
    import nrplanner as _pkg
    items_json_path = (
        Path(_pkg.__file__).parent / "resources" / "json" / "items.json"
    )
    items_json = json.loads(items_json_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmpdir:
        decrypt_sl2(FIXTURE_PATH, tmpdir)
        characters = discover_characters(tmpdir)
        assert characters, "No characters found in save fixture"
        _, char_path = characters[0]
        data = char_path.read_bytes()
        raw_relics, _ = parse_relics(data)
    return RelicInventory(raw_relics, items_json, ds)


def _print_result(label: str, result, idx: int = 0) -> None:
    print(f"  Result {idx}: score={result.total_score}")
    for a in result.assignments:
        if a.relic:
            eff_strs = [f"{e['name']}={e['score']}" for e in a.breakdown]
            print(f"    Slot {a.slot_index} ({a.slot_color}): "
                  f"{a.relic.name} [{a.score} pts] - {', '.join(eff_strs)}")


@pytest.mark.skipif(
    not FIXTURE_PATH.exists(),
    reason="Real save fixture not present",
)
class TestDiagnosticSolver:

    def test_hybrid_solver_with_prefilter(
        self, ds: SourceDataHandler, real_inventory: RelicInventory,
    ) -> None:
        """Verify the hybrid solver (greedy + seeded backtrack) produces optimal results."""
        build = _make_guardian_build()
        scorer = BuildScorer(ds)
        optimizer = VesselOptimizer(ds, scorer)

        # Find Guardian's Chalice vessel
        vessels = list(ds.get_all_vessels_for_hero(2))
        chalice = None
        for v in vessels:
            if "Chalice" in v["Name"]:
                chalice = dict(v)
                chalice["_id"] = v["vessel_id"]
                break

        assert chalice is not None, "Guardian's Chalice not found"
        print(f"\n{'='*70}")
        print(f"Vessel: {chalice['Name']}")
        print(f"Colors: {chalice['Colors']}")
        print(f"Total relics in inventory: {len(real_inventory.relics)}")

        # Run the full optimizer.optimize() which now uses the hybrid approach
        import time
        t0 = time.perf_counter()
        results = optimizer.optimize(build, real_inventory, chalice, top_n=3)
        elapsed = time.perf_counter() - t0

        print(f"\n--- HYBRID SOLVER (greedy + seeded backtrack) [{elapsed*1000:.0f}ms] ---")
        for idx, result in enumerate(results):
            _print_result("hybrid", result, idx)

        assert results, "No results returned"
        best = results[0] if results else None
        print(f"\n  BEST SCORE: {best.total_score}")
        print(f"{'='*70}")

        # Gold standard brute force proves 62 is optimal for this build/vessel.
        assert best.total_score >= 62, (
            f"Expected score >= 62, got {best.total_score}. "
            f"Optimizer is still suboptimal."
        )
