"""Tests for nrplanner.cumulative — cumulative stacked-effect summaries.

Effect ids are derived at runtime from the loaded family data (not hardcoded)
so the behavioural tests survive game-data id changes; the data-integrity test
guards effect_bonus_values.json against typos / stale ids directly.
"""
import orjson
import pytest

from nrplanner.cumulative import summarize_cumulative_effects
from nrplanner.data import SourceDataHandler


def _ids_by_mag(ds: SourceDataHandler, family: str) -> dict[int, list[int]]:
    """{tier_magnitude: [effect_ids]} for a family base name."""
    ds._ensure_families()
    fam = ds._effect_families[family]
    return {m["magnitude"]: sorted(m["effect_ids"]) for m in fam["members"]}


def test_headline_magic_attack_worked_example(ds: SourceDataHandler):
    """The user's example: Magic Attack +4 x3, +2 x1, +1 x1 -> 1.58x (+58%)."""
    mag = _ids_by_mag(ds, "Magic Attack Power Up")
    placed = [mag[4][0], mag[4][0], mag[4][0], mag[2][0], mag[1][0]]

    groups = summarize_cumulative_effects(placed, ds)

    assert len(groups) == 1
    g = groups[0]
    assert g.family == "Magic Attack Power Up"
    assert g.mode == "multiplicative"
    assert g.is_top is True
    assert g.cumulative_value == pytest.approx(1.12**3 * 1.065 * 1.055)
    assert g.bonus_percent == pytest.approx(57.85, abs=0.1)
    assert g.bonus_display == "1.58× (+58%)"
    # tiers strongest-first with correct counts
    assert [(t.tier_label, t.count) for t in g.tiers] == [("+4", 3), ("+2", 1), ("+1", 1)]


def test_additive_flat_vigor(ds: SourceDataHandler):
    mag = _ids_by_mag(ds, "Vigor")
    groups = summarize_cumulative_effects([mag[1][0], mag[2][0]], ds)

    assert len(groups) == 1
    g = groups[0]
    assert g.mode == "additive_flat"
    assert g.cumulative_value == 60.0
    assert g.bonus_percent is None
    assert g.bonus_display == "+60 Max HP"


def test_multiplicative_reduction_negation(ds: SourceDataHandler):
    """Two +10% magic negations stack multiplicatively: 1-(0.9*0.9)=19%."""
    mag = _ids_by_mag(ds, "Improved Magic Damage Negation")
    base_id = mag[0][0]  # the +10% base
    groups = summarize_cumulative_effects([base_id, base_id], ds)

    assert len(groups) == 1
    g = groups[0]
    assert g.mode == "multiplicative_reduction"
    assert g.cumulative_value == pytest.approx(0.19)
    assert g.bonus_percent == pytest.approx(19.0)
    assert g.bonus_display == "+19% Magic Negation"


def test_single_copy_still_summarized(ds: SourceDataHandler):
    mag = _ids_by_mag(ds, "Magic Attack Power Up")
    groups = summarize_cumulative_effects([mag[4][0]], ds)
    assert len(groups) == 1
    assert groups[0].bonus_display == "1.12× (+12%)"
    assert groups[0].tiers[0].count == 1


def test_collision_ids_merge_into_one_tier(ds: SourceDataHandler):
    """Physical Attack Up +2 maps to two ids; placing both = +2 x2, not two tiers."""
    mag = _ids_by_mag(ds, "Physical Attack Up")
    plus2 = mag[2]
    if len(plus2) < 2:
        pytest.skip("no +2 collision in current data")
    groups = summarize_cumulative_effects([plus2[0], plus2[1]], ds)
    assert len(groups) == 1
    g = groups[0]
    assert len(g.tiers) == 1
    assert g.tiers[0].tier_label == "+2"
    assert g.tiers[0].count == 2
    assert g.cumulative_value == pytest.approx(1.06**2)


def test_top_line_prefers_percentage_over_flat(ds: SourceDataHandler):
    """Mixed vessel: a multiplicative group outranks a flat-stat group for 'top'."""
    atk = _ids_by_mag(ds, "Magic Attack Power Up")
    vig = _ids_by_mag(ds, "Vigor")
    groups = summarize_cumulative_effects([atk[4][0], vig[1][0], vig[2][0]], ds)
    top = next(g for g in groups if g.is_top)
    assert top.bonus_percent is not None  # a percentage group, not the flat Vigor


def test_top_line_falls_back_to_flat_when_no_percentage(ds: SourceDataHandler):
    vig = _ids_by_mag(ds, "Vigor")
    groups = summarize_cumulative_effects([vig[1][0], vig[2][0]], ds)
    top = next(g for g in groups if g.is_top)
    assert top.mode == "additive_flat"


def test_non_clean_effects_ignored(ds: SourceDataHandler):
    """An id with no curated value contributes no group; clean ids still summarize."""
    atk = _ids_by_mag(ds, "Magic Attack Power Up")
    bogus = 999_999_999
    assert ds.get_effect_bonus_value(bogus) is None
    groups = summarize_cumulative_effects([atk[4][0], bogus], ds)
    assert len(groups) == 1
    assert groups[0].family == "Magic Attack Power Up"


def test_empty_input(ds: SourceDataHandler):
    assert summarize_cumulative_effects([], ds) == []


def test_data_file_integrity(ds: SourceDataHandler):
    """Every curated id resolves to a real effect with a valid spec (catches stale ids).

    A real name (not 'Empty' / 'Effect <n>') is the stale-id guard; family
    membership is NOT required since family-less singletons (weapon-class powers,
    'Improved Damage Negation at Low HP') are legitimately curated.
    """
    path = ds._resources_dir / "json" / "effect_bonus_values.json"
    raw = orjson.loads(path.read_bytes())
    valid_modes = {"multiplicative", "multiplicative_reduction", "additive_flat"}

    checked = 0
    for key, spec in raw.items():
        if key.startswith("_"):
            continue
        eid = int(key)
        name = ds.get_effect_name(eid)
        assert name not in ("Empty",) and not name.startswith("Effect "), \
            f"{eid} has no real name ({name!r})"
        assert spec["mode"] in valid_modes, f"{eid} bad mode {spec['mode']!r}"
        if "conditional" in spec:
            assert isinstance(spec["conditional"], str) and spec["conditional"], \
                f"{eid} ({name!r}) has empty conditional"
        assert ds.get_effect_bonus_value(eid) == spec  # round-trips through the loader
        checked += 1
    assert checked > 100  # sanity: the table is populated


def _singleton_id(ds: SourceDataHandler, name: str) -> int:
    """First curated effect id whose resolved name == ``name`` (family-less singleton)."""
    import orjson as _oj

    raw = _oj.loads((ds._resources_dir / "json" / "effect_bonus_values.json").read_bytes())
    for key in raw:
        if key.startswith("_"):
            continue
        eid = int(key)
        if ds.get_effect_name(eid) == name:
            return eid
    raise KeyError(name)


def test_singleton_weapon_power_stacks_five(ds: SourceDataHandler):
    """5× Improved Katana Attack Power (1.09×) compounds: 1.09**5 -> +54%."""
    katana = _singleton_id(ds, "Improved Katana Attack Power")
    groups = summarize_cumulative_effects([katana] * 5, ds)

    assert len(groups) == 1
    g = groups[0]
    assert g.family == "Improved Katana Attack Power"
    assert g.mode == "multiplicative"
    assert g.cumulative_value == pytest.approx(1.09 ** 5)
    assert g.bonus_display == "1.54× (+54%)"
    assert g.conditional is None
    # lone singleton -> no "+0" tier label, just the count
    assert len(g.tiers) == 1
    assert g.tiers[0].tier_label == ""
    assert g.tiers[0].count == 5


def test_conditional_low_hp_negation_badged(ds: SourceDataHandler):
    """The user's example: 5× Improved Damage Negation at Low HP, carries condition."""
    low_hp = _singleton_id(ds, "Improved Damage Negation at Low HP")
    groups = summarize_cumulative_effects([low_hp] * 5, ds)

    assert len(groups) == 1
    g = groups[0]
    assert g.mode == "multiplicative_reduction"
    # 1 - 0.84**5 = 58.2%
    assert g.cumulative_value == pytest.approx(1 - 0.84 ** 5)
    assert g.bonus_display == "+58% Damage Negation"
    assert g.conditional == "when HP below 40%"


def test_deep_max_hp_multiplier_shows_unit(ds: SourceDataHandler):
    """Increased Maximum HP is multiplicative but reads as a percent of Max HP."""
    max_hp = _singleton_id(ds, "Increased Maximum HP")
    groups = summarize_cumulative_effects([max_hp, max_hp], ds)
    g = groups[0]
    assert g.mode == "multiplicative"
    assert g.cumulative_value == pytest.approx(1.1 ** 2)
    assert g.bonus_display == "+21% Max HP"


def test_max_hp_only_deep_stacking_id_is_curated(ds: SourceDataHandler):
    """'Increased Maximum HP' is two effects sharing a name: the deep-pool 1.1x
    multiplier (stack) and the regular-pool +100-flat boost (no_stack, '+5 vigor').
    Only the self-stacking id may carry a value — a single per-id (value, mode)
    can't describe both pools, and the no_stack version never stacks anyway."""
    import json

    effects = json.loads(
        (ds._resources_dir / "json" / "effects.json").read_text(encoding="utf-8")
    )
    ids = [int(k) for k in effects if ds.get_effect_name(int(k)) == "Increased Maximum HP"]
    stack_ids = [e for e in ids if ds.get_effect_stacking_type(e) == "stack"]
    nostack_ids = [e for e in ids if ds.get_effect_stacking_type(e) == "no_stack"]
    assert stack_ids and nostack_ids  # both pools exist in the data

    for e in stack_ids:
        assert ds.get_effect_bonus_value(e) is not None, f"{e} (deep stack) should be curated"
    for e in nostack_ids:
        assert ds.get_effect_bonus_value(e) is None, f"{e} (regular no_stack) must NOT be curated"


def test_plus0_base_unifies_with_family(ds: SourceDataHandler):
    """The suffix-less 'Improved Sorceries' base merges into the +1/+2 family group."""
    base = _singleton_id(ds, "Improved Sorceries")  # +0 = 1.05×
    plus = _ids_by_mag(ds, "Improved Sorceries")     # +1 = 1.085×, +2 = 1.1×
    groups = summarize_cumulative_effects([base, plus[1][0], plus[2][0]], ds)

    assert len(groups) == 1  # one unified family group, not a singleton + a family
    g = groups[0]
    assert g.family == "Improved Sorceries"
    assert g.cumulative_value == pytest.approx(1.05 * 1.085 * 1.1)
    labels = {t.tier_label for t in g.tiers}
    assert labels == {"+0", "+1", "+2"}  # base labelled "+0" because the group is tiered
