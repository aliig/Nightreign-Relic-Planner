"""
Relic Build Optimizer - Core Logic
Scores relics against user-defined build priorities and finds optimal
relic assignments for vessel slots.
"""
import uuid
import time
import pathlib
from dataclasses import dataclass, field
from typing import Optional

import orjson

from globals import COLOR_MAP, RELIC_GROUPS
from source_data_handler import SourceDataHandler


# Tier weights for scoring
TIER_WEIGHTS = {
    "must_have": 100,
    "nice_to_have": 50,
    "low_priority": 20,
    "blacklist": -200,
}

# Small bonus for relics with more effect slots (tiebreaker)
TIER_BONUS = {3: 5, 2: 2, 1: 0, 0: 0}

EMPTY_EFFECT = 4294967295


@dataclass
class OwnedRelic:
    """A relic owned by the player, parsed from save data."""
    ga_handle: int
    item_id: int       # raw item_id from save
    real_id: int        # item_id - 2147483648
    color: str          # "Red", "Blue", "Yellow", "Green", "White"
    effects: list       # [effect_1, effect_2, effect_3]
    curses: list        # [sec_effect1, sec_effect2, sec_effect3]
    is_deep: bool
    name: str
    tier: str           # "Grand", "Polished", "Delicate"

    @property
    def effect_count(self) -> int:
        return sum(1 for e in self.effects if e != EMPTY_EFFECT and e != 0)

    @property
    def curse_count(self) -> int:
        return sum(1 for c in self.curses if c != EMPTY_EFFECT and c != 0)

    @property
    def all_effects(self) -> list:
        """All non-empty effects and curses combined."""
        return [e for e in self.effects + self.curses
                if e != EMPTY_EFFECT and e != 0]


class RelicInventory:
    """Queryable collection of owned relics."""

    def __init__(self, ga_relics: list, items_json: dict,
                 data_source: SourceDataHandler):
        self.relics: list[OwnedRelic] = []
        self._build(ga_relics, items_json, data_source)

    def _build(self, ga_relics: list, items_json: dict,
               data_source: SourceDataHandler):
        for r in ga_relics:
            ga_handle = r[0]
            item_id = r[1]
            real_id = item_id - 2147483648
            effects = [r[2], r[3], r[4]]
            curses = [r[5], r[6], r[7]]

            # Look up color and name
            id_str = str(real_id)
            info = items_json.get(id_str, {})
            color = info.get("color", "Red")
            name = info.get("name", f"Relic {real_id}")

            if color is None:
                continue  # Skip non-relic items (Flatstones, etc.)

            is_deep = data_source.is_deep_relic(real_id)

            # Determine tier by non-empty effect count
            effect_count = sum(1 for e in effects
                               if e != EMPTY_EFFECT and e != 0)
            if effect_count >= 3:
                tier = "Grand"
            elif effect_count == 2:
                tier = "Polished"
            else:
                tier = "Delicate"

            self.relics.append(OwnedRelic(
                ga_handle=ga_handle,
                item_id=item_id,
                real_id=real_id,
                color=color,
                effects=effects,
                curses=curses,
                is_deep=is_deep,
                name=name,
                tier=tier,
            ))

    def get_by_color(self, color: str) -> list[OwnedRelic]:
        return [r for r in self.relics if r.color == color]

    def get_standard(self) -> list[OwnedRelic]:
        return [r for r in self.relics if not r.is_deep]

    def get_deep(self) -> list[OwnedRelic]:
        return [r for r in self.relics if r.is_deep]

    def get_candidates(self, slot_color: str, is_deep_slot: bool,
                       curse_tolerance: int = 3) -> list[OwnedRelic]:
        """Get relics eligible for a specific slot."""
        results = []
        for r in self.relics:
            # Standard/deep type must match
            if is_deep_slot != r.is_deep:
                continue
            # Color must match (White slots accept any)
            if slot_color != "White" and r.color != slot_color:
                continue
            # Curse tolerance for deep relics
            if is_deep_slot and r.curse_count > curse_tolerance:
                continue
            results.append(r)
        return results

    def __len__(self):
        return len(self.relics)


@dataclass
class BuildDefinition:
    """A user-defined build configuration."""
    id: str
    name: str
    character: str
    tiers: dict = field(default_factory=lambda: {
        "must_have": [],
        "nice_to_have": [],
        "low_priority": [],
        "blacklist": [],
    })
    include_deep: bool = True
    curse_tolerance: int = 1

    def all_prioritized_effects(self) -> set:
        """All effect IDs across all tiers."""
        result = set()
        for tier_effects in self.tiers.values():
            result.update(tier_effects)
        return result

    def get_tier_for_effect(self, effect_id: int) -> Optional[str]:
        """Return the tier name an effect belongs to, or None."""
        for tier_name, effects in self.tiers.items():
            if effect_id in effects:
                return tier_name
        return None


class BuildStore:
    """Persists build definitions to JSON."""

    def __init__(self, base_dir: pathlib.Path):
        self.file_path = base_dir / "optimizer_builds.json"
        self.builds: dict[str, BuildDefinition] = {}
        self._load()

    def _load(self):
        if not self.file_path.exists():
            return
        try:
            data = orjson.loads(self.file_path.read_bytes())
            for build_id, b in data.get("builds", {}).items():
                self.builds[build_id] = BuildDefinition(
                    id=build_id,
                    name=b["name"],
                    character=b["character"],
                    tiers=b.get("tiers", {
                        "must_have": [],
                        "nice_to_have": [],
                        "low_priority": [],
                        "blacklist": [],
                    }),
                    include_deep=b.get("include_deep", True),
                    curse_tolerance=b.get("curse_tolerance", 1),
                )
        except Exception as e:
            print(f"[BuildStore] Error loading builds: {e}")

    def save(self):
        data = {
            "version": 1,
            "builds": {}
        }
        for build_id, b in self.builds.items():
            data["builds"][build_id] = {
                "name": b.name,
                "character": b.character,
                "tiers": b.tiers,
                "include_deep": b.include_deep,
                "curse_tolerance": b.curse_tolerance,
            }
        self.file_path.write_bytes(
            orjson.dumps(data, option=orjson.OPT_INDENT_2)
        )

    def create(self, name: str, character: str) -> BuildDefinition:
        build_id = str(uuid.uuid4())[:8]
        build = BuildDefinition(id=build_id, name=name, character=character)
        self.builds[build_id] = build
        self.save()
        return build

    def delete(self, build_id: str):
        if build_id in self.builds:
            del self.builds[build_id]
            self.save()

    def rename(self, build_id: str, new_name: str):
        if build_id in self.builds:
            self.builds[build_id].name = new_name
            self.save()

    def update(self, build: BuildDefinition):
        self.builds[build.id] = build
        self.save()

    def get(self, build_id: str) -> Optional[BuildDefinition]:
        return self.builds.get(build_id)

    def list_builds(self) -> list[BuildDefinition]:
        return list(self.builds.values())


class BuildScorer:
    """Scores relics against a build definition."""

    def __init__(self, data_source: SourceDataHandler):
        self.data_source = data_source

    def score_relic(self, relic: OwnedRelic,
                    build: BuildDefinition) -> int:
        """Score a single relic against the build's priorities."""
        score = 0

        # Score normal effects
        for eff in relic.effects:
            if eff == EMPTY_EFFECT or eff == 0:
                continue
            tier = build.get_tier_for_effect(eff)
            if tier:
                score += TIER_WEIGHTS[tier]

        # Score curse effects (only blacklist applies as penalty)
        for curse in relic.curses:
            if curse == EMPTY_EFFECT or curse == 0:
                continue
            tier = build.get_tier_for_effect(curse)
            if tier:
                score += TIER_WEIGHTS[tier]

        # Tier bonus (tiebreaker favoring relics with more effects)
        score += TIER_BONUS.get(relic.effect_count, 0)

        return score

    def get_breakdown(self, relic: OwnedRelic,
                      build: BuildDefinition) -> list[dict]:
        """Detailed per-effect scoring for UI display."""
        breakdown = []
        for eff in relic.effects:
            if eff == EMPTY_EFFECT or eff == 0:
                continue
            tier = build.get_tier_for_effect(eff)
            name = self.data_source.get_effect_name(eff)
            breakdown.append({
                "effect_id": eff,
                "name": name,
                "tier": tier,
                "score": TIER_WEIGHTS.get(tier, 0) if tier else 0,
                "is_curse": False,
            })

        for curse in relic.curses:
            if curse == EMPTY_EFFECT or curse == 0:
                continue
            tier = build.get_tier_for_effect(curse)
            name = self.data_source.get_effect_name(curse)
            breakdown.append({
                "effect_id": curse,
                "name": name,
                "tier": tier,
                "score": TIER_WEIGHTS.get(tier, 0) if tier else 0,
                "is_curse": True,
            })

        return breakdown


@dataclass
class SlotAssignment:
    """A relic assigned to a specific vessel slot."""
    slot_index: int
    slot_color: str
    is_deep: bool
    relic: Optional[OwnedRelic]
    score: int
    breakdown: list


@dataclass
class VesselResult:
    """Optimization result for a single vessel."""
    vessel_id: int
    vessel_name: str
    vessel_character: str
    unlock_flag: int
    slot_colors: tuple  # 6-tuple of color strings
    assignments: list   # list[SlotAssignment]
    total_score: int


class VesselOptimizer:
    """Finds optimal relic assignment for vessel slots."""

    def __init__(self, data_source: SourceDataHandler, scorer: BuildScorer):
        self.data_source = data_source
        self.scorer = scorer

    def _get_conflict_ids(self, relic: OwnedRelic) -> set:
        """Get all non-negative compatibilityIds for a relic's effects."""
        conflicts = set()
        for eff in relic.all_effects:
            cid = self.data_source.get_effect_conflict_id(eff)
            if cid != -1:
                conflicts.add(cid)
        return conflicts

    def _has_conflict(self, relic: OwnedRelic,
                      active_conflicts: set) -> bool:
        """Check if a relic conflicts with already-assigned relics."""
        for eff in relic.all_effects:
            cid = self.data_source.get_effect_conflict_id(eff)
            if cid != -1 and cid in active_conflicts:
                return True
        return False

    def optimize(self, build: BuildDefinition,
                 inventory: RelicInventory,
                 vessel_data: dict) -> VesselResult:
        """Find best relic assignment for a single vessel."""
        slot_colors = vessel_data["Colors"]  # 6-tuple
        num_slots = 6 if build.include_deep else 3

        # Build candidate lists per slot
        candidates_per_slot = []
        for i in range(num_slots):
            is_deep = i >= 3
            slot_color = slot_colors[i]
            candidates = inventory.get_candidates(
                slot_color, is_deep, build.curse_tolerance
            )
            # Score and sort descending
            scored = []
            for relic in candidates:
                score = self.scorer.score_relic(relic, build)
                scored.append((score, relic))
            scored.sort(key=lambda x: x[0], reverse=True)
            candidates_per_slot.append(scored)

        # Count total candidates for deciding algorithm
        total_candidates = sum(len(c) for c in candidates_per_slot)

        if total_candidates <= 200 and num_slots <= 6:
            assignments = self._backtrack_solve(
                candidates_per_slot, num_slots, build
            )
        else:
            assignments = self._greedy_solve(
                candidates_per_slot, num_slots, build
            )

        # Build result
        slot_assignments = []
        total_score = 0
        for i in range(num_slots):
            is_deep = i >= 3
            slot_color = slot_colors[i]
            relic, score = assignments[i]
            breakdown = []
            if relic:
                breakdown = self.scorer.get_breakdown(relic, build)
            slot_assignments.append(SlotAssignment(
                slot_index=i,
                slot_color=slot_color,
                is_deep=is_deep,
                relic=relic,
                score=score,
                breakdown=breakdown,
            ))
            total_score += score

        return VesselResult(
            vessel_id=vessel_data.get("_id", 0),
            vessel_name=vessel_data["Name"],
            vessel_character=vessel_data["Character"],
            unlock_flag=vessel_data["unlockFlag"],
            slot_colors=slot_colors,
            assignments=slot_assignments,
            total_score=total_score,
        )

    def _greedy_solve(self, candidates_per_slot: list, num_slots: int,
                      build: BuildDefinition) -> list:
        """Greedy assignment: pick best available relic per slot."""
        # Merge all (score, slot_index, relic) and sort descending
        all_options = []
        for slot_idx, scored_list in enumerate(candidates_per_slot):
            for score, relic in scored_list:
                all_options.append((score, slot_idx, relic))
        all_options.sort(key=lambda x: x[0], reverse=True)

        assigned = [None] * num_slots  # (relic, score) per slot
        used_handles = set()
        active_conflicts = set()

        for score, slot_idx, relic in all_options:
            if assigned[slot_idx] is not None:
                continue
            if relic.ga_handle in used_handles:
                continue
            if self._has_conflict(relic, active_conflicts):
                continue

            assigned[slot_idx] = (relic, score)
            used_handles.add(relic.ga_handle)
            active_conflicts.update(self._get_conflict_ids(relic))

        # Fill empty slots
        for i in range(num_slots):
            if assigned[i] is None:
                assigned[i] = (None, 0)

        return assigned

    def _backtrack_solve(self, candidates_per_slot: list, num_slots: int,
                         build: BuildDefinition) -> list:
        """Exhaustive search with pruning for small candidate sets."""
        best_score = [-1]
        best_assignment = [None]
        start_time = time.time()
        timeout = 2.0  # seconds

        def backtrack(slot_idx, current_assignment, used_handles,
                      active_conflicts, current_score):
            if time.time() - start_time > timeout:
                return  # Timeout, use best found so far

            if slot_idx == num_slots:
                if current_score > best_score[0]:
                    best_score[0] = current_score
                    best_assignment[0] = list(current_assignment)
                return

            # Try assigning no relic to this slot
            current_assignment[slot_idx] = (None, 0)
            backtrack(slot_idx + 1, current_assignment, used_handles,
                      active_conflicts, current_score)

            # Try each candidate
            for score, relic in candidates_per_slot[slot_idx]:
                if relic.ga_handle in used_handles:
                    continue
                if self._has_conflict(relic, active_conflicts):
                    continue

                # Prune: even if remaining slots all scored max possible,
                # can we beat best?
                remaining_max = sum(
                    candidates_per_slot[s][0][0]
                    if candidates_per_slot[s] else 0
                    for s in range(slot_idx + 1, num_slots)
                )
                if current_score + score + remaining_max <= best_score[0]:
                    continue

                # Assign
                new_conflicts = self._get_conflict_ids(relic)
                current_assignment[slot_idx] = (relic, score)
                used_handles.add(relic.ga_handle)
                active_conflicts.update(new_conflicts)

                backtrack(slot_idx + 1, current_assignment, used_handles,
                          active_conflicts, current_score + score)

                # Unassign
                used_handles.discard(relic.ga_handle)
                active_conflicts -= new_conflicts

        initial = [(None, 0)] * num_slots
        backtrack(0, initial, set(), set(), 0)

        if best_assignment[0] is None:
            return [(None, 0)] * num_slots
        return best_assignment[0]

    def optimize_all_vessels(self, build: BuildDefinition,
                             inventory: RelicInventory,
                             hero_type: int) -> list[VesselResult]:
        """Try all vessels for a character, return top results sorted by score."""
        vessels = self.data_source.get_all_vessels_for_hero(hero_type)
        results = []

        for v in vessels:
            vessel_data = v.copy()
            vessel_data["_id"] = v["vessel_id"]
            result = self.optimize(build, inventory, vessel_data)
            result.vessel_id = v["vessel_id"]
            results.append(result)

        results.sort(key=lambda r: r.total_score, reverse=True)
        return results
