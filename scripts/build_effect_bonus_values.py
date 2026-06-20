"""Generate nrplanner/resources/json/effect_bonus_values.json.

The human-curated source of truth is SPEC below: per effect *family* (using the
codebase's own family base names, see SourceDataHandler._effect_families), the
in-game bonus value per tier magnitude. We resolve those to raw effect_ids via
the live family data so collisions (e.g. Physical Attack Up +2 == 7001402 AND
7001409) and alias ids are captured automatically, then emit a flat
{effect_id: {value, mode, unit}} table.

Values transcribed from the community spreadsheet (NORMAL +0/+1/+2 and DEEP
+3/+4 tabs). Mostly "Stackable with self? = Yes" rows, plus the "unique"
multi-tier families whose different levels stack (e.g. vs-afflicted-enemy
damage) -- for those a copy does not stack with itself (cumulative.py caps
duplicate copies of one tier) but different tiers do. Modes:
  multiplicative           value = per-copy damage multiplier   (1.12 -> +12%)
  multiplicative_reduction value = per-copy reduction fraction  (0.10 -> -10% taken)
  additive_flat            value = per-copy flat amount; unit names it

Run:  uv run python scripts/build_effect_bonus_values.py
"""
import json
from pathlib import Path

from nrplanner.data import SourceDataHandler

MULT = "multiplicative"
RED = "multiplicative_reduction"
FLAT = "additive_flat"

# family base name -> (mode, unit, {tier_magnitude: value})
SPEC: dict[str, tuple[str, str, dict[int, float]]] = {
    # --- multiplicative attack / damage ---
    "Magic Attack Power Up":    (MULT, "%", {0: 1.045, 1: 1.055, 2: 1.065, 3: 1.105, 4: 1.12}),
    "Fire Attack Power Up":     (MULT, "%", {0: 1.045, 1: 1.055, 2: 1.065, 3: 1.105, 4: 1.12}),
    "Lightning Attack Power Up": (MULT, "%", {0: 1.045, 1: 1.055, 2: 1.065, 3: 1.105, 4: 1.12}),
    "Holy Attack Power Up":     (MULT, "%", {0: 1.045, 1: 1.055, 2: 1.065, 3: 1.105, 4: 1.12}),
    "Physical Attack Up":       (MULT, "%", {0: 1.04, 1: 1.05, 2: 1.06, 3: 1.105, 4: 1.12}),
    "Improved Affinity Attack Power": (MULT, "%", {1: 1.08, 2: 1.1}),
    "Improved Guard Counters":  (MULT, "%", {0: 1.17, 1: 1.25, 2: 1.29}),
    "Improved Sorceries":       (MULT, "%", {1: 1.085, 2: 1.1}),
    "Improved Incantations":    (MULT, "%", {1: 1.085, 2: 1.1}),
    "Improved Throwing Pot Damage":   (MULT, "%", {0: 1.15, 1: 1.3}),
    "Improved Throwing Knife Damage": (MULT, "%", {0: 1.15, 1: 1.3}),
    "Improved Perfuming Arts":  (MULT, "%", {0: 1.15, 1: 1.3}),
    "Ultimate Art Auto Charge": (MULT, "%", {1: 1.05, 2: 1.075, 3: 1.1}),

    # --- "unique" vs-afflicted-enemy damage (different levels stack, a copy does
    #     NOT stack with itself -- see stacking_rules.json). +0 = 1.1x (normal
    #     pool), +1/+2 = 1.16x/1.2x (deep pool). ---
    "Attack power up when facing poison-afflicted enemy":      (MULT, "%", {0: 1.1, 1: 1.16, 2: 1.2}),
    "Attack power up when facing scarlet rot-afflicted enemy": (MULT, "%", {0: 1.1, 1: 1.16, 2: 1.2}),
    "Attack power up when facing frostbite-afflicted enemy":   (MULT, "%", {0: 1.1, 1: 1.16, 2: 1.2}),

    # --- multiplicative reduction (negation / poise / cooldown) ---
    "Improved Magic Damage Negation":     (RED, "Magic Negation", {0: 0.10, 1: 0.15, 2: 0.16}),
    "Improved Fire Damage Negation":      (RED, "Fire Negation", {0: 0.10, 1: 0.15, 2: 0.16}),
    "Improved Lightning Damage Negation": (RED, "Lightning Negation", {0: 0.10, 1: 0.15, 2: 0.16}),
    "Improved Holy Damage Negation":      (RED, "Holy Negation", {0: 0.10, 1: 0.15, 2: 0.16}),
    "Improved Physical Damage Negation":  (RED, "Physical Negation", {0: 0.10, 1: 0.105, 2: 0.12}),
    "Improved Affinity Damage Negation":  (RED, "Affinity Negation", {1: 0.105, 2: 0.12}),
    "Poise":                              (RED, "Poise Damage Reduction", {1: 0.05, 2: 0.10, 3: 0.15}),
    "Character Skill Cooldown Reduction": (RED, "Skill Cooldown Reduction", {1: 0.05, 2: 0.075, 3: 0.10}),

    # --- additive flat ---
    "Vigor":      (FLAT, "Max HP", {1: 20, 2: 40, 3: 60}),
    "Mind":       (FLAT, "Max FP", {1: 5, 2: 10, 3: 15}),
    "Endurance":  (FLAT, "Max Stamina", {1: 2, 2: 4, 3: 6}),
    "Strength":     (FLAT, "Strength", {1: 1, 2: 2, 3: 3}),
    "Dexterity":    (FLAT, "Dexterity", {1: 1, 2: 2, 3: 3}),
    "Intelligence": (FLAT, "Intelligence", {1: 1, 2: 2, 3: 3}),
    "Faith":        (FLAT, "Faith", {1: 1, 2: 2, 3: 3}),
    "Arcane":       (FLAT, "Arcane", {1: 1, 2: 2, 3: 3}),
    "Improved Blood Loss Resistance":   (FLAT, "Resistance", {0: 75, 1: 110, 2: 130}),
    "Improved Frost Resistance":        (FLAT, "Resistance", {0: 75, 1: 110, 2: 130}),
    "Improved Death Blight Resistance": (FLAT, "Resistance", {0: 75, 1: 110, 2: 130}),
    "Improved Madness Resistance":      (FLAT, "Resistance", {0: 75, 1: 110, 2: 130}),
    "Improved Poison Resistance":       (FLAT, "Resistance", {0: 75, 1: 110, 2: 130}),
    "Improved Rot Resistance":          (FLAT, "Resistance", {0: 75, 1: 110, 2: 130}),
    "Improved Sleep Resistance":        (FLAT, "Resistance", {0: 75, 1: 110, 2: 130}),
}

# Self-stackable effects that the family builder does NOT group into a +N family
# (no numeric magnitude suffix, so get_effect_family() returns None). Keyed by the
# exact resolved effect name; resolved to raw ids via a name->ids reverse map.
#   name -> (mode, unit, per_copy_value, condition | None)
# When a name here also matches an existing FAMILY base (the four "+0 base"
# entries below), its id is the suffix-less base; cumulative.py groups it under
# the same family name, so it unifies with the family's +1/+2 tiers.
SINGLETON_SPEC: dict[str, tuple[str, str, float, str | None]] = {
    # --- weapon-type attack power (1.09x; bows 1.06x) ---
    "Improved Axe Attack Power":                  (MULT, "%", 1.09, None),
    "Improved Bow Attack Power":                  (MULT, "%", 1.06, None),
    "Improved Claw Attack Power":                 (MULT, "%", 1.09, None),
    "Improved Colossal Sword Attack Power":       (MULT, "%", 1.09, None),
    "Improved Colossal Weapon Attack Power":      (MULT, "%", 1.09, None),
    "Improved Curved Greatsword Attack Power":    (MULT, "%", 1.09, None),
    "Improved Curved Sword Attack Power":         (MULT, "%", 1.09, None),
    "Improved Dagger Attack Power":               (MULT, "%", 1.09, None),
    "Improved Fist Attack Power":                 (MULT, "%", 1.09, None),
    "Improved Flail Attack Power":                (MULT, "%", 1.09, None),
    "Improved Great Hammer Attack Power":         (MULT, "%", 1.09, None),
    "Improved Great Spear Attack Power":          (MULT, "%", 1.09, None),
    "Improved Greataxe Attack Power":             (MULT, "%", 1.09, None),
    "Improved Greatsword Attack Power":           (MULT, "%", 1.09, None),
    "Improved Halberd Attack Power":              (MULT, "%", 1.09, None),
    "Improved Hammer Attack Power":               (MULT, "%", 1.09, None),
    "Improved Heavy Thrusting Sword Attack Power": (MULT, "%", 1.09, None),
    "Improved Katana Attack Power":               (MULT, "%", 1.09, None),
    "Improved Reaper Attack Power":               (MULT, "%", 1.09, None),
    "Improved Spear Attack Power":                (MULT, "%", 1.09, None),
    "Improved Straight Sword Attack Power":       (MULT, "%", 1.09, None),
    "Improved Thrusting Sword Attack Power":      (MULT, "%", 1.09, None),
    "Improved Twinblade Attack Power":            (MULT, "%", 1.09, None),
    "Improved Whip Attack Power":                 (MULT, "%", 1.09, None),

    # --- spell-school sorceries / incantations (1.12x) ---
    "Improved Bestial Incantations":          (MULT, "%", 1.12, None),
    "Improved Carian Sword Sorcery":          (MULT, "%", 1.12, None),
    "Improved Crystalian sorcery":            (MULT, "%", 1.12, None),
    "Improved Dragon Communion Incantations": (MULT, "%", 1.12, None),
    "Improved Dragon Cult Incantations":      (MULT, "%", 1.12, None),
    "Improved Frenzied Flame Incantations":   (MULT, "%", 1.12, None),
    "Improved Fundamentalist Incantations":   (MULT, "%", 1.12, None),
    "Improved Giants' Flame Incantations":    (MULT, "%", 1.12, None),
    "Improved Glintblade Sorcery":            (MULT, "%", 1.12, None),
    "Improved Godslayer Incantations":        (MULT, "%", 1.12, None),
    "Improved Gravity Sorcery":               (MULT, "%", 1.12, None),
    "Improved Invisibility Sorcery":          (MULT, "%", 1.12, None),
    "Improved Stonedigger Sorcery":           (MULT, "%", 1.12, None),
    "Improved Thorn Sorcery":                 (MULT, "%", 1.12, None),

    # --- broad / misc multiplicative attack ---
    "Improved Melee Attack Power":                  (MULT, "%", 1.05, None),
    "Improved Skill Attack Power":                  (MULT, "%", 1.15, None),
    "Improved Initial Standard Attack":             (MULT, "%", 1.15, None),
    "Improved Roar & Breath Attacks":               (MULT, "%", 1.15, None),
    "Boosts Attack Power of Added Affinity Attacks": (MULT, "%", 1.10, None),

    # --- +0 bases of existing families (suffix-less; unify by shared name) ---
    "Improved Affinity Attack Power":   (MULT, "%", 1.05, None),
    "Improved Sorceries":               (MULT, "%", 1.05, None),
    "Improved Incantations":            (MULT, "%", 1.05, None),
    "Improved Affinity Damage Negation": (RED, "Affinity Negation", 0.06, None),

    # --- elemental "Damage Negation Up" singletons (10% reduction each) ---
    "Magic Damage Negation Up":     (RED, "Magic Negation", 0.10, None),
    "Fire Damage Negation Up":      (RED, "Fire Negation", 0.10, None),
    "Lightning Damage Negation Up": (RED, "Lightning Negation", 0.10, None),
    "Holy Damage Negation Up":      (RED, "Holy Negation", 0.10, None),

    # --- max-stat multipliers (deep pool; per-copy multiplier on the stat) ---
    "Increased Maximum HP":      (MULT, "Max HP", 1.10, None),
    "Increased Maximum FP":      (MULT, "Max FP", 1.15, None),
    "Increased Maximum Stamina": (MULT, "Max Stamina", 1.12, None),

    # --- conditional (badged in UI; not always-on) ---
    "Improved Damage Negation at Low HP": (RED, "Damage Negation", 0.16, "when HP below 40%"),
}


def _name_to_ids(ds: SourceDataHandler) -> dict[str, list[int]]:
    """Reverse map of resolved effect name -> all ids that render to it."""
    import json as _json

    effects = _json.loads(
        (Path(ds._resources_dir) / "json" / "effects.json").read_text(encoding="utf-8")
    )
    out: dict[str, list[int]] = {}
    for key in effects:
        eid = int(key)
        try:
            out.setdefault(ds.get_effect_name(eid), []).append(eid)
        except Exception:
            continue
    return out


def main() -> None:
    ds = SourceDataHandler()
    ds._ensure_families()

    table: dict[int, dict] = {}
    warnings: list[str] = []

    for base, (mode, unit, tiers) in SPEC.items():
        fam = ds._effect_families.get(base)
        if not fam:
            warnings.append(f"FAMILY NOT FOUND: {base!r}")
            continue
        seen_mags = set()
        for member in fam["members"]:
            mag = member["magnitude"]
            if mag not in tiers:
                warnings.append(f"{base}: member mag{mag} ({member['name']!r}) has no spec value")
                continue
            seen_mags.add(mag)
            for eid in member["effect_ids"]:
                table[int(eid)] = {"value": tiers[mag], "mode": mode, "unit": unit}
        for mag in tiers:
            if mag not in seen_mags:
                warnings.append(f"{base}: spec tier mag{mag} matched no family member")

    # Family-less singletons resolved by exact name. A display name can map to
    # several ids that behave differently by pool — e.g. "Increased Maximum HP"
    # is the deep-pool 1.1x multiplier (stack) AND the regular-pool +100 flat
    # boost (no_stack, "+5 vigor"). Only tag the self-stacking ids, since the
    # whole table is about self-stacking totals and a single per-id (value, mode)
    # can't describe both pools.
    name_to_ids = _name_to_ids(ds)
    for name, (mode, unit, value, cond) in SINGLETON_SPEC.items():
        all_ids = name_to_ids.get(name) or []
        ids = [e for e in all_ids if ds.get_effect_stacking_type(e) == "stack"]
        if not ids:
            kind = "no self-stacking ids" if all_ids else "name not found"
            warnings.append(f"SINGLETON SKIPPED ({kind}): {name!r}")
            continue
        entry: dict[str, object] = {"value": value, "mode": mode, "unit": unit}
        if cond is not None:
            entry["conditional"] = cond
        for eid in ids:
            table[int(eid)] = dict(entry)

    out: dict[str, object] = {
        "_comment": (
            "GENERATED by scripts/build_effect_bonus_values.py — edit SPEC there, not this file. "
            "Keys are effect_ids. mode 'multiplicative': value=per-copy damage multiplier (1.12=+12%). "
            "'multiplicative_reduction': value=per-copy reduction fraction (0.10=-10% taken), stacks as "
            "1-prod(1-v). 'additive_flat': value=per-copy flat amount named by unit. Numeric effects that "
            "stack with self, plus 'unique' multi-tier families whose different levels stack (duplicate "
            "copies of one tier do not -- cumulative.py caps them); absent id => no computed total. Optional "
            "'conditional' marks effects that only apply in a context (e.g. 'when HP below 40%') so the UI can badge them."
        ),
        "_source": "community spreadsheet NORMAL+DEEP tabs: Stackable-with-self=Yes rows + 'different levels stack' unique families",
    }
    for eid in sorted(table):
        out[str(eid)] = table[eid]

    dest = Path(ds._resources_dir) / "json" / "effect_bonus_values.json"
    dest.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Wrote {len(table)} effect ids -> {dest}")
    if warnings:
        print("\nWARNINGS:")
        for w in warnings:
            print("  - " + w)
    else:
        print("No warnings.")


if __name__ == "__main__":
    main()
