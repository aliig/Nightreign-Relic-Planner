"""Generate nrplanner/resources/json/effect_bonus_values.json.

The human-curated source of truth is SPEC below: per effect *family* (using the
codebase's own family base names, see SourceDataHandler._effect_families), the
in-game bonus value per tier magnitude. We resolve those to raw effect_ids via
the live family data so collisions (e.g. Physical Attack Up +2 == 7001402 AND
7001409) and alias ids are captured automatically, then emit a flat
{effect_id: {value, mode, unit}} table.

Values transcribed from the community spreadsheet (NORMAL +0/+1/+2 and DEEP
+3/+4 tabs), "Stackable with self? = Yes" rows only. Modes:
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

    out: dict[str, object] = {
        "_comment": (
            "GENERATED by scripts/build_effect_bonus_values.py — edit SPEC there, not this file. "
            "Keys are effect_ids. mode 'multiplicative': value=per-copy damage multiplier (1.12=+12%). "
            "'multiplicative_reduction': value=per-copy reduction fraction (0.10=-10% taken), stacks as "
            "1-prod(1-v). 'additive_flat': value=per-copy flat amount named by unit. Clean, unconditional, "
            "self-stackable numeric effects only; absent id => no computed total."
        ),
        "_source": "community spreadsheet NORMAL+DEEP tabs, Stackable-with-self=Yes rows",
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
