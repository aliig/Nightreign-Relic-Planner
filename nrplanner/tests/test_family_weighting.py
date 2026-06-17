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
