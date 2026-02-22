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
    required_keys = {"id", "name"}
    for effect in all_effects:
        assert required_keys.issubset(effect.keys()), f"Missing keys in: {effect}"


def test_get_all_vessels_for_hero(ds: SourceDataHandler) -> None:
    # hero_type 100000 = Wylder
    vessels = ds.get_all_vessels_for_hero(100000)
    assert isinstance(vessels, list)
    assert len(vessels) > 0
    first = vessels[0]
    assert "vessel_id" in first


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
