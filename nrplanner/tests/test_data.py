"""Tests for SourceDataHandler (data.py).

Key goal: verify that resource files load correctly on any OS.
The Json→json directory rename was a real Linux/Docker casing bug;
test_constructs_without_error catches regressions of that class.
"""
import pytest

from nrplanner import SourceDataHandler


def test_constructs_without_error(ds: SourceDataHandler) -> None:
    """SourceDataHandler initialises with real resource files — no FileNotFoundError."""
    assert ds is not None
    # Both core tables must be non-empty DataFrames
    assert len(ds.get_relic_datas()) > 0
    assert len(ds.get_effect_datas()) > 0


def test_get_safe_relic_ids_returns_list_of_ints(
    ds: SourceDataHandler, safe_relic_ids: list[int]
) -> None:
    assert len(safe_relic_ids) > 0
    assert all(isinstance(i, int) for i in safe_relic_ids)
    # None of the safe IDs should be in the illegal range (20000–30035)
    for relic_id in safe_relic_ids:
        assert not (20000 <= relic_id <= 30035), f"Illegal ID in safe list: {relic_id}"


def test_get_relic_pools_seq_valid_id(
    ds: SourceDataHandler, safe_relic_ids: list[int]
) -> None:
    relic_id = safe_relic_ids[0]
    pools = ds.get_relic_pools_seq(relic_id)
    assert isinstance(pools, list)
    assert len(pools) == 6
    assert all(isinstance(p, int) for p in pools)


def test_get_relic_pools_seq_invalid_id_raises(ds: SourceDataHandler) -> None:
    with pytest.raises((KeyError, Exception)):
        ds.get_relic_pools_seq(99999)


def test_get_effect_name_returns_string(
    ds: SourceDataHandler, all_effects: list[dict]
) -> None:
    eff_id = all_effects[0]["id"]
    name = ds.get_effect_name(eff_id)
    assert isinstance(name, str)
    assert name != ""
    assert name != "Empty"
    assert not name.startswith("Effect ")


def test_get_effect_name_empty_sentinel(ds: SourceDataHandler) -> None:
    assert ds.get_effect_name(4294967295) == "Empty"


def test_is_deep_relic_true(ds: SourceDataHandler) -> None:
    # 2000000 is the start of the deep_102 range
    assert SourceDataHandler.is_deep_relic(2000000) is True


def test_is_deep_relic_false(ds: SourceDataHandler) -> None:
    # 100 is in the store_102 range (standard relics)
    assert SourceDataHandler.is_deep_relic(100) is False


def test_get_all_effects_list_structure(
    ds: SourceDataHandler, all_effects: list[dict]
) -> None:
    assert len(all_effects) > 0
    required_keys = {"id", "name", "alias_ids"}
    for effect in all_effects:
        assert required_keys.issubset(effect.keys()), f"Missing keys in: {effect}"
        assert isinstance(effect["alias_ids"], list), "alias_ids must be a list"


def test_get_all_effects_list_covers_all_valid_ids(
    ds: SourceDataHandler, all_effects: list[dict]
) -> None:
    """Every valid effect ID in AttachEffectParam must be reachable via id or alias_ids.

    Regression guard for the deduplication bug where get_all_effects_list() dropped
    alias IDs, causing relics whose effects used those IDs to show blank properties.
    """
    # Build the set of all IDs reachable from the exported effects list
    reachable: set[int] = set()
    for e in all_effects:
        reachable.add(e["id"])
        reachable.update(e["alias_ids"])

    # Compute all valid IDs from the raw param table (same filter as get_all_effects_list)
    expected: set[int] = set()
    for eff_id in ds.effect_params.index:
        if eff_id == 0:
            continue
        name = ds.get_effect_name(eff_id).strip()
        if name == "Empty" or name.startswith("Effect "):
            continue
        expected.add(eff_id)

    missing = expected - reachable
    assert not missing, (
        f"{len(missing)} valid effect IDs are not reachable via id or alias_ids: "
        f"{sorted(missing)[:20]}{'...' if len(missing) > 20 else ''}"
    )


def test_get_all_vessels_for_hero(ds: SourceDataHandler) -> None:
    # hero_type 1 = Wylder (1-based CSV index, NOT the NPC text file ID)
    vessels = ds.get_all_vessels_for_hero(1)
    assert isinstance(vessels, list)
    assert len(vessels) > 0
    first = vessels[0]
    assert "vessel_id" in first


def test_get_all_vessels_for_hero_includes_character_specific(
    ds: SourceDataHandler,
) -> None:
    """Querying with a valid hero index must return character-specific vessels,
    not only the shared 'All' vessels (heroType=11)."""
    vessels = ds.get_all_vessels_for_hero(1)  # Wylder
    characters = {v["Character"] for v in vessels}
    assert "Wylder" in characters, (
        f"Expected Wylder-specific vessels but got characters: {characters}"
    )
    # Should also include shared vessels
    assert "All" in characters


def test_reload_text_en_us(ds: SourceDataHandler) -> None:
    result = ds.reload_text("en_US")
    assert result is True


def test_get_effect_stacking_type(
    ds: SourceDataHandler, all_effects: list[dict]
) -> None:
    eff_id = all_effects[0]["id"]
    stype = ds.get_effect_stacking_type(eff_id)
    assert stype in ("stack", "unique", "no_stack")


def test_get_support_languages_includes_en_us(ds: SourceDataHandler) -> None:
    languages = ds.get_support_languages()
    assert "en_US" in languages


def test_effect_families_require_at_least_two_resolved_members(
    ds: SourceDataHandler,
) -> None:
    """Every family returned by get_all_families_list() must have 2+ members with IDs.

    Regression: stacking_rules.json entries like 'Improved Damage Negation at Low HP'
    have +1/+2 variants that lack effect params, so after ID resolution those members
    are pruned to nothing — leaving a single-member 'family'. The fix ensures such
    entries are excluded from the families list entirely.
    """
    families = ds.get_all_families_list()
    for fam in families:
        assert len(fam["member_names"]) >= 2, (
            f"Family '{fam['name']}' has only {len(fam['member_names'])} resolved member(s); "
            "expected 2+. Single-member families should not be exposed as families."
        )


def test_effect_families_known_single_member_effects_are_not_families(
    ds: SourceDataHandler,
) -> None:
    """Specific effects known to have no real magnitude variants must not be families.

    These had +1/+2 entries in stacking_rules.json but no matching effect params,
    so only the base variant resolves — making them singletons, not families.
    """
    known_non_families = [
        "Improved Damage Negation at Low HP",
        "Improved Roar & Breath Attacks",
        "Reduced FP Consumption",
        "Critical Hits Earn Runes",
        "Taking attacks improves attack power",
    ]
    family_names = {fam["name"] for fam in ds.get_all_families_list()}
    for name in known_non_families:
        assert name not in family_names, (
            f"'{name}' should not appear as an effect family (no resolved magnitude variants)"
        )
