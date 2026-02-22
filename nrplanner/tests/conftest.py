"""Shared fixtures for nrplanner unit tests.

SourceDataHandler loads all game CSVs + XML FMGs from the bundled resources.
It is expensive to construct (~500 ms), so it is session-scoped.
All test data (relic IDs, effect IDs) is derived at runtime from the
loaded data to ensure tests remain valid across game data updates.
"""
import pytest

from nrplanner import SourceDataHandler


@pytest.fixture(scope="session")
def ds() -> SourceDataHandler:
    """Real SourceDataHandler using bundled game resources. Loaded once per run."""
    return SourceDataHandler(language="en_US")


@pytest.fixture(scope="session")
def safe_relic_ids(ds: SourceDataHandler) -> list[int]:
    """IDs of relics that are safe to use in tests (not in the illegal range)."""
    return ds.get_safe_relic_ids()


@pytest.fixture(scope="session")
def all_effects(ds: SourceDataHandler) -> list[dict]:
    """All effect dicts from the game data: {id, name, ...}."""
    return ds.get_all_effects_list()
