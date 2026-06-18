"""Tests for proportional-to-top family weight scaling (Phase 2).

get_family_magnitude_weight scales a family weight to each tier by the tier's
real in-game bonus relative to the family's strongest tier, with a linear
rank/total fallback for families lacking curated bonus values.
"""
import pytest

from nrplanner.data import SourceDataHandler


def _id(ds: SourceDataHandler, family: str, mag: int) -> int:
    ds._ensure_families()
    for m in ds._effect_families[family]["members"]:
        if m["magnitude"] == mag:
            return sorted(m["effect_ids"])[0]
    raise KeyError((family, mag))


def test_proportional_to_top_magic_attack(ds: SourceDataHandler):
    """The user-approved split: +0..+4 -> 37/45/54/87/100 at family weight 100."""
    fam = "Magic Attack Power Up"
    got = {mag: ds.get_family_magnitude_weight(_id(ds, fam, mag), 100) for mag in range(5)}
    assert got == {0: 37, 1: 45, 2: 54, 3: 87, 4: 100}


def test_non_linear_resistance(ds: SourceDataHandler):
    """Additive but non-linear (75/110/130) -> 57/84/100, not rank/total 33/67/100."""
    fam = "Improved Blood Loss Resistance"
    got = {mag: ds.get_family_magnitude_weight(_id(ds, fam, mag), 100) for mag in range(3)}
    assert got == {0: 57, 1: 84, 2: 100}


def test_negation_reduction_scaling(ds: SourceDataHandler):
    """Reduction fractions 0.10/0.15/0.16 -> 62/93/100 of top."""
    fam = "Improved Magic Damage Negation"
    got = {mag: ds.get_family_magnitude_weight(_id(ds, fam, mag), 100) for mag in range(3)}
    assert got == {0: 62, 1: 93, 2: 100}


def test_top_tier_gets_full_weight(ds: SourceDataHandler):
    for fam in ("Fire Attack Power Up", "Vigor", "Poise", "Improved Sleep Resistance"):
        members = ds._effect_families[fam]["members"]
        top_id = sorted(members[-1]["effect_ids"])[0]  # members sorted ascending by magnitude
        assert ds.get_family_magnitude_weight(top_id, 100) == 100


def test_uncurated_family_uses_rank_total(ds: SourceDataHandler):
    """A family with no curated values keeps the old linear rank/total scaling."""
    fam = "Improved Glintstone and Gravity Stone Damage"
    members = ds._effect_families[fam]["members"]
    assert ds.get_effect_bonus_value(sorted(members[0]["effect_ids"])[0]) is None
    total = len(members)
    for rank, m in enumerate(members, 1):
        eid = sorted(m["effect_ids"])[0]
        assert ds.get_family_magnitude_weight(eid, 100) == int(100 * rank / total)


def test_unknown_effect_returns_base_weight(ds: SourceDataHandler):
    assert ds.get_family_magnitude_weight(999_999_999, 77) == 77


def test_families_list_exposes_weight_fractions(ds: SourceDataHandler):
    fams = {f["name"]: f for f in ds.get_all_families_list()}
    magic = fams["Magic Attack Power Up"]
    fracs = {t["name"]: t["weight_fraction"] for t in magic["tiers"]}
    assert fracs["Magic Attack Power Up"] == pytest.approx(0.375, abs=0.001)  # +0
    assert fracs["Magic Attack Power Up +4"] == 1.0


# ---------------------------------------------------------------------------
# Per-family weight floor (rescale-to-floor)
# ---------------------------------------------------------------------------

def test_rescale_to_floor_formula():
    """Static helper maps [frac_min, 1] onto [f_eff, base] and reduces to default."""
    r = SourceDataHandler._rescale_to_floor
    # Strongest tier (frac == 1) always maps to the full family weight.
    assert r(100, 1.0, 0.0, 50) == 100
    # Weakest tier (frac == frac_min) maps exactly to the floor.
    assert r(100, 0.0, 0.0, 50) == 50
    assert r(100, 0.375, 0.375, 50) == 50
    # Linear in between.
    assert r(100, 0.5, 0.0, 50) == 75
    # A floor at the natural minimum (base*frac_min) is a no-op == default base*frac.
    assert r(100, 0.5, 0.375, 37) == 50   # f_eff clamps up to natural_min 37.5
    assert r(100, 0.8, 0.375, 0) == 80    # == int(100 * 0.8)
    # A floor cannot exceed the family weight.
    assert r(100, 0.375, 0.375, 200) == 100
    # Single-tier family (frac_min == 1.0): nothing to scale.
    assert r(100, 1.0, 1.0, 50) == 100


def test_floor_zero_is_noop(ds: SourceDataHandler):
    """floor=0 reproduces the default proportional-to-top scaling exactly."""
    fam = "Magic Attack Power Up"
    default = {m: ds.get_family_magnitude_weight(_id(ds, fam, m), 100) for m in range(5)}
    floored0 = {m: ds.get_family_magnitude_weight(_id(ds, fam, m), 100, 0) for m in range(5)}
    assert default == {0: 37, 1: 45, 2: 54, 3: 87, 4: 100}
    assert floored0 == default


def test_floor_rescales_weakest_to_floor(ds: SourceDataHandler):
    """floor=50: weakest tier -> 50, strongest stays 100, strictly increasing (no ties)."""
    fam = "Magic Attack Power Up"
    got = {m: ds.get_family_magnitude_weight(_id(ds, fam, m), 100, 50) for m in range(5)}
    assert got[0] == 50          # weakest tier sits exactly on the floor
    assert got[4] == 100         # strongest tier unchanged
    vals = [got[m] for m in range(5)]
    assert vals == sorted(vals)  # monotonic
    assert len(set(vals)) == 5   # rescale spreads tiers — unlike a clamp, no ties
    # a floor never lowers a tier below its natural value
    default = {m: ds.get_family_magnitude_weight(_id(ds, fam, m), 100) for m in range(5)}
    assert all(got[m] >= default[m] for m in range(5))


def test_floor_below_natural_minimum_is_noop(ds: SourceDataHandler):
    """A floor at/below the weakest tier's natural value changes nothing."""
    fam = "Magic Attack Power Up"
    default = {m: ds.get_family_magnitude_weight(_id(ds, fam, m), 100) for m in range(5)}
    # +0 naturally scores 37; a floor of 20 is below that, so it is inert.
    got = {m: ds.get_family_magnitude_weight(_id(ds, fam, m), 100, 20) for m in range(5)}
    assert got == default


def test_floor_ignored_for_negative_weight(ds: SourceDataHandler):
    """Penalty (negative-weight) families ignore a positive points floor."""
    fam = "Magic Attack Power Up"
    for m in range(5):
        eid = _id(ds, fam, m)
        assert (ds.get_family_magnitude_weight(eid, -100, 50)
                == ds.get_family_magnitude_weight(eid, -100, 0))


def test_floor_on_uncurated_family(ds: SourceDataHandler):
    """Rescale also applies to the rank/total fallback (uncurated families)."""
    fam = "Improved Glintstone and Gravity Stone Damage"
    members = ds._effect_families[fam]["members"]
    weakest = sorted(members[0]["effect_ids"])[0]
    strongest = sorted(members[-1]["effect_ids"])[0]
    assert ds.get_effect_bonus_value(weakest) is None  # confirm uncurated
    assert ds.get_family_magnitude_weight(weakest, 100, 60) == 60
    assert ds.get_family_magnitude_weight(strongest, 100, 60) == 100
