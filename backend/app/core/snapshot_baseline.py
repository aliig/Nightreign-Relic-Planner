"""The baseline a build's changes are measured from, and what moved since it.

An OptimizationSnapshot stores two different things about a build+slot:

- the CURRENT run — layouts, results, freshness hashes — which every run
  overwrites, because it is the cache;
- the BASELINE — the arrangement and inputs the user last *saw and
  acknowledged* — which only advances when there is nothing left to tell them.

They used to be the same row, so every run re-baselined and each change was
measured against the previous run rather than against the last state the user
actually reviewed.  A player who uploaded a save and then spent a million Murk
in Relic Rites got two half-stories (save→save, then save→purchases), neither
of which was shown: the first was overwritten before it was read, the second
was classified as a staged edit and suppressed.  With a sticky baseline the two
compose into one honest verdict — "since the save you last looked at, here is
where your build stands" — and the causes list names *everything* that moved.

The baseline is a single JSON blob rather than a column per field: nothing
filters or joins on it, and the freshness hashes it duplicates stay in their own
columns where the cache-hit query needs them.
"""
from typing import Any

from nrplanner.models import ChangeCause

# Causes worth telling the user about.  A build edit or a game-data bump is the
# user's own action (or ours) rather than news about their save, so a run caused
# only by those re-baselines silently — exactly as before.
NARRATABLE: frozenset[str] = frozenset({"relics", "staged"})


def snapshot_inputs(
    *,
    base_relics_hash: str,
    relics_hash: str,
    build_hash: str,
    game_data_version: str,
    optimizer_version: str,
    staged_signature: str | None,
) -> dict[str, Any]:
    """The inputs an optimization result depends on, as a comparable dict.

    ``relics_hash`` is the EFFECTIVE inventory (what the optimizer actually ran
    on) and ``base_relics_hash`` is the save's own inventory with the staged
    diff excluded.  Keeping both is what lets cause attribution separate "your
    save changed" from "you bought relics in the app": a staged purchase moves
    the effective hash but never the base one.
    """
    return {
        "base_relics_hash": base_relics_hash,
        "relics_hash": relics_hash,
        "build_hash": build_hash,
        "game_data_version": game_data_version,
        "optimizer_version": optimizer_version,
        "staged_signature": staged_signature,
    }


def make_baseline(
    *, layouts: list[dict], best_score: int, inputs: dict[str, Any]
) -> dict[str, Any]:
    """A baseline record: the arrangement plus the inputs that produced it."""
    return {"layouts": layouts, "best_score": best_score, "inputs": dict(inputs)}


def baseline_layouts(baseline: dict[str, Any] | None) -> list[dict] | None:
    """The stored arrangement to diff against (None on a first-ever run)."""
    if not baseline:
        return None
    return baseline.get("layouts") or None


def causes_since(
    baseline: dict[str, Any] | None, inputs: dict[str, Any]
) -> list[ChangeCause]:
    """Every input that differs between the baseline and this run.

    Order is fixed (relics, staged, build_edit, game_data) so the list is stable
    for tests and display.  An empty list means nothing moved.

    Two details earn their asymmetry:

    - "relics" compares the BASE hashes, so a staged purchase (which also moves
      the effective hash) is never mistaken for a newer save.
    - "staged" is only claimed when this run actually carries a staged diff.
      Losing one the other way — baseline had a diff, this run has none —
      is what exporting and re-uploading looks like: those relics are in the
      save now, and the base hash moved to say so.
    """
    if not baseline:
        return []
    old = baseline.get("inputs") or {}
    out: list[ChangeCause] = []
    if old.get("base_relics_hash") != inputs["base_relics_hash"]:
        out.append("relics")
    if (
        inputs["staged_signature"] is not None
        and old.get("staged_signature") != inputs["staged_signature"]
    ):
        out.append("staged")
    if old.get("build_hash") != inputs["build_hash"]:
        out.append("build_edit")
    if (
        old.get("game_data_version") != inputs["game_data_version"]
        or old.get("optimizer_version") != inputs["optimizer_version"]
    ):
        out.append("game_data")
    return out


def is_narratable(causes: list[str]) -> bool:
    """Whether this change is news for the user (vs. their own edit)."""
    return any(c in NARRATABLE for c in causes)


def legacy_cause(causes: list[str]) -> str | None:
    """Coarse single-value summary of ``causes`` for pre-list clients.

    Kept so an older frontend keeps behaving as it did: one cause passes
    through as itself, several collapse to "mixed".
    """
    if not causes:
        return None
    if len(causes) == 1:
        return causes[0]
    return "mixed"
