"""
Game data loader — reads CSV/XML resource files into queryable DataFrames.

All methods are read-only. Constructor takes an optional resources_dir so
paths can be overridden (useful for testing and future FastAPI deployment).
"""
import re
from pathlib import Path
from typing import Optional, Union

import pandas as pd

from nrplanner.constants import (
    COLOR_MAP, LANGUAGE_MAP, CHARACTER_NAME_ID, CHARACTER_NAMES, RELIC_GROUPS,
)


def get_system_language() -> str:
    """Detect OS locale and return a supported language code (default en_US)."""
    import locale
    try:
        lang, _ = locale.getdefaultlocale()
    except Exception:
        return "en_US"
    if lang:
        import locale as _lc
        normalized = _lc.normalize(lang).split('.')[0].replace('-', '_')
        if normalized in LANGUAGE_MAP:
            return normalized
    return "en_US"


def _filter_nonzero_weight(effects: pd.DataFrame) -> pd.DataFrame:
    """Keep only effects with a non-zero final chanceWeight.

    chanceWeight_dlc == -1 means use base chanceWeight.
    """
    e = effects.copy()
    return e[(e["chanceWeight_dlc"] > 0) |
             ((e["chanceWeight"] != 0) & (e["chanceWeight_dlc"] == -1))]


class SourceDataHandler:
    """Loads and queries all game reference data (relics, effects, vessels)."""

    RELIC_TEXT_FILES  = ["AntiqueName.fmg.xml",       "AntiqueName_dlc01.fmg.xml"]
    EFFECT_TEXT_FILES = ["AttachEffectName.fmg.xml",   "AttachEffectName_dlc01.fmg.xml"]
    NPC_TEXT_FILES    = ["NpcName.fmg.xml",            "NpcName_dlc01.fmg.xml"]
    GOODS_TEXT_FILES  = ["GoodsName.fmg.xml",          "GoodsName_dlc01.fmg.xml"]

    def __init__(self, language: str = "en_US",
                 resources_dir: Path | None = None):
        if resources_dir is None:
            resources_dir = Path(__file__).parent / "resources"
        self._resources_dir = resources_dir
        self._param_dir = resources_dir / "param"
        self._text_dir  = resources_dir / "text"

        self.effect_params: pd.DataFrame = (
            pd.read_csv(self._param_dir / "AttachEffectParam.csv")
            [["ID", "compatibilityId", "attachTextId", "overrideEffectId"]]
            .set_index("ID")
        )
        self.effect_table: pd.DataFrame = (
            pd.read_csv(self._param_dir / "AttachEffectTableParam.csv")
            [["ID", "attachEffectId", "chanceWeight", "chanceWeight_dlc"]]
        )
        self.relic_table: pd.DataFrame = (
            pd.read_csv(self._param_dir / "EquipParamAntique.csv")
            [[
                "ID", "relicColor",
                "attachEffectTableId_1", "attachEffectTableId_2", "attachEffectTableId_3",
                "attachEffectTableId_curse1", "attachEffectTableId_curse2", "attachEffectTableId_curse3",
            ]]
            .set_index("ID")
        )
        self.antique_stand_param: pd.DataFrame = pd.read_csv(
            self._param_dir / "AntiqueStandParam.csv"
        )

        # Text DataFrames — populated by _load_text
        self.relic_name:   Optional[pd.DataFrame] = None
        self.effect_name:  Optional[pd.DataFrame] = None
        self.npc_name:     Optional[pd.DataFrame] = None
        self.vessel_names: Optional[pd.DataFrame] = None
        # Relic IDs added in patch 1.03 (Scene relics)
        self._scene_relic_ids: set = set()
        # Per-instance localized character names (avoids mutating the module constant)
        self.character_names: list[str] = list(CHARACTER_NAMES)

        self._load_text(language)

    # ------------------------------------------------------------------
    # Text / language
    # ------------------------------------------------------------------

    def _load_text(self, language: str = "en_US") -> None:
        lng = language if language in LANGUAGE_MAP else "en_US"

        def _read_xml(filename: str) -> pd.DataFrame:
            return pd.read_xml(
                self._text_dir / lng / filename, xpath="/fmg/entries/text"
            )

        # Relic names — track Scene relic IDs from dlc01 file
        relic_df: Optional[pd.DataFrame] = None
        self._scene_relic_ids = set()
        for fname in self.RELIC_TEXT_FILES:
            df = _read_xml(fname)
            if "_dlc01" in fname:
                self._scene_relic_ids.update(df[df["text"] != "%null%"]["id"].tolist())
            relic_df = df if relic_df is None else pd.concat([relic_df, df])

        # Effect names
        effect_df: Optional[pd.DataFrame] = None
        for fname in self.EFFECT_TEXT_FILES:
            df = _read_xml(fname)
            effect_df = df if effect_df is None else pd.concat([effect_df, df])

        # NPC names → localized character_names list
        npc_df: Optional[pd.DataFrame] = None
        for fname in self.NPC_TEXT_FILES:
            df = _read_xml(fname)
            npc_df = df if npc_df is None else pd.concat([npc_df, df])

        self.character_names = list(CHARACTER_NAMES)  # reset to English defaults
        for npc_id in CHARACTER_NAME_ID:
            matches = npc_df[npc_df["id"] == npc_id]["text"].tolist()
            if matches:
                idx = CHARACTER_NAME_ID.index(npc_id)
                self.character_names[idx] = matches[0]

        # Vessel names from GoodsName (IDs 9600-9956)
        goods_df: Optional[pd.DataFrame] = None
        for fname in self.GOODS_TEXT_FILES:
            df = _read_xml(fname)
            goods_df = df if goods_df is None else pd.concat([goods_df, df])
        self.vessel_names = goods_df[
            (goods_df["id"] >= 9600) &
            (goods_df["id"] <= 9956) &
            (goods_df["text"] != "%null%")
        ]

        self.relic_name  = relic_df
        self.effect_name = effect_df
        self.npc_name    = npc_df

    def reload_text(self, language: str = "en_US") -> bool:
        """Reload text for a new language. Returns True on success."""
        try:
            self._load_text(language)
            return True
        except (FileNotFoundError, KeyError):
            self._load_text()
            return False

    def get_support_languages(self) -> dict[str, str]:
        return LANGUAGE_MAP

    def get_support_languages_code(self):
        return LANGUAGE_MAP.keys()

    def get_support_languages_name(self):
        return LANGUAGE_MAP.values()

    # ------------------------------------------------------------------
    # Relic data
    # ------------------------------------------------------------------

    def get_relic_origin_structure(self) -> dict:
        """Return {str(relic_id): {name, color}} for all relics."""
        names = self.relic_name.copy().set_index("id").rename(columns={"text": "name"})
        result = {}
        for idx, row in self.relic_table.iterrows():
            name_matches  = names[names.index == idx]["name"].values
            color_matches = self.relic_table[self.relic_table.index == idx]["relicColor"].values
            result[str(idx)] = {
                "name":  str(name_matches[0]) if len(name_matches) > 0 else "Unset",
                "color": COLOR_MAP[int(color_matches[0])] if len(color_matches) > 0 else "Red",
            }
        return result

    def get_relic_datas(self) -> pd.DataFrame:
        names = self.relic_name.copy().rename(columns={"text": "name"})
        result = self.relic_table.copy().reset_index()
        result = pd.merge(result, names, how="left", left_on="ID", right_on="id")
        result.drop(columns=["id"], inplace=True)
        return result.set_index("ID")

    def get_relic_color(self, relic_id: int) -> str:
        return COLOR_MAP[self.relic_table.loc[relic_id, "relicColor"]]

    def get_relic_type_info(self, relic_id: int) -> tuple:
        if self.is_scene_relic(relic_id):
            return ("Scene Relic (1.03+)", "Unique effect pools from patch 1.03", "#9966CC")
        return ("Original Relic", "Effect pools from base game", "#666666")

    def is_scene_relic(self, relic_id: int) -> bool:
        return relic_id in self._scene_relic_ids

    def get_relic_pools_seq(self, relic_id: int) -> list:
        return self.relic_table.loc[relic_id, [
            "attachEffectTableId_1", "attachEffectTableId_2", "attachEffectTableId_3",
            "attachEffectTableId_curse1", "attachEffectTableId_curse2", "attachEffectTableId_curse3",
        ]].values.tolist()

    def get_relic_slot_count(self, relic_id: int) -> tuple[int, int]:
        seq = self.get_relic_pools_seq(relic_id)
        return 3 - seq[:3].count(-1), 3 - seq[3:].count(-1)

    def get_filtered_relics_df(self, color: Union[int, str] = None,
                               deep: Optional[bool] = None,
                               effect_slot: Optional[int] = None,
                               curse_slot: Optional[int] = None) -> pd.DataFrame:
        df = self.relic_table.copy().reset_index()
        df = df[df["ID"].isin(self.get_safe_relic_ids())]
        if color is not None:
            color_id = COLOR_MAP.index(color) if isinstance(color, str) else color
            df = df[df["relicColor"] == color_id]
        if deep is not None:
            mask = df["ID"].apply(self.is_deep_relic)
            df = df[mask] if deep else df[~mask]
        if effect_slot is not None:
            df = df[df["ID"].apply(lambda x: self.get_relic_slot_count(x)[0] == effect_slot)]
        if curse_slot is not None:
            df = df[df["ID"].apply(lambda x: self.get_relic_slot_count(x)[1] == curse_slot)]
        return df

    @staticmethod
    def get_safe_relic_ids() -> list[int]:
        safe = ["store_102", "store_103",
                "reward_0", "reward_1", "reward_2", "reward_3", "reward_4",
                "reward_5", "reward_6", "reward_7", "reward_8", "reward_9",
                "deep_102", "deep_103"]
        ids = []
        for name in safe:
            lo, hi = RELIC_GROUPS[name]
            ids.extend(range(lo, hi + 1))
        return ids

    @staticmethod
    def is_deep_relic(relic_id: int) -> bool:
        lo1, hi1 = RELIC_GROUPS["deep_102"]
        lo2, hi2 = RELIC_GROUPS["deep_103"]
        return lo1 <= relic_id <= hi1 or lo2 <= relic_id <= hi2

    # ------------------------------------------------------------------
    # Effect data
    # ------------------------------------------------------------------

    def get_effect_origin_structure(self) -> dict:
        """Return {str(effect_id): {name}} for all effects."""
        names = self.effect_name.copy().set_index("id")
        result = {"4294967295": {"name": "Empty"}}
        for idx, row in self.effect_params.iterrows():
            try:
                text_id = self.effect_params.loc[idx, "attachTextId"]
                matches = names[names.index == text_id]["text"].values
                result[str(idx)] = {"name": str(matches[0]) if len(matches) > 0 else "Unknown"}
            except KeyError:
                result[str(idx)] = {"name": "Unknown"}
        return result

    def get_effect_datas(self) -> pd.DataFrame:
        names = self.effect_name.copy().rename(columns={"text": "name"})
        result = self.effect_params.copy().reset_index()
        result = pd.merge(result, names, how="left", left_on="attachTextId", right_on="id")
        result.drop(columns=["id"], inplace=True)
        result.set_index("ID", inplace=True)
        result.fillna({"name": "Unknown"}, inplace=True)
        return result

    def get_effect_name(self, effect_id: int) -> str:
        if effect_id in (-1, 0, 4294967295):
            return "Empty"
        try:
            row = self.effect_name[self.effect_name["id"] == effect_id]
            if not row.empty:
                text = str(row["text"].values[0]).strip()
                if text != "%null%":
                    return text
            if effect_id in self.effect_params.index:
                text_id = int(self.effect_params.loc[effect_id, "attachTextId"])
                if text_id != -1:
                    row = self.effect_name[self.effect_name["id"] == text_id]
                    if not row.empty:
                        return str(row["text"].values[0]).strip()
        except Exception:
            pass
        return f"Effect {effect_id}"

    def get_effect_text_id(self, effect_id: int) -> int:
        """Return attachTextId (canonical text ID) for an effect, or -1."""
        try:
            if effect_id in (-1, 0, 4294967295):
                return -1
            if effect_id in self.effect_params.index:
                return int(self.effect_params.loc[effect_id, "attachTextId"])
        except (KeyError, ValueError):
            pass
        return -1

    def get_effect_conflict_id(self, effect_id: int) -> int:
        try:
            if effect_id in (-1, 4294967295):
                return -1
            return int(self.effect_params.loc[effect_id, "compatibilityId"])
        except KeyError:
            return -1

    def get_sort_id(self, effect_id: int) -> int:
        try:
            return int(self.effect_params.loc[effect_id, "overrideEffectId"])
        except KeyError:
            return -1

    def _get_source_override_names(self) -> set[str]:
        """Lazy-load the set of effect names that have source overrides."""
        if hasattr(self, "_source_override_names"):
            return self._source_override_names
        import orjson
        rules_path = self._resources_dir / "json" / "stacking_rules.json"
        try:
            if rules_path.exists():
                rules = orjson.loads(rules_path.read_bytes())
                self._source_override_names = set(rules.get("_source_overrides", {}).keys())
            else:
                self._source_override_names = set()
        except Exception:
            self._source_override_names = set()
        return self._source_override_names

    def get_all_effects_list(self) -> list[dict]:
        """All effects with metadata for the build UI.

        Deduplicates by display name, preferring entries where param_id == text_id.
        Effects with source overrides (different stacking for deep vs regular
        pools) are kept as separate entries with source='deep'.
        """
        source_override_names = self._get_source_override_names()

        full = pd.read_csv(self._param_dir / "AttachEffectParam.csv")
        char_cols = ["allowWylder", "allowGuardian", "allowIroneye", "allowDuchess",
                     "allowRaider", "allowRevenant", "allowRecluse", "allowExecutor",
                     "allowScholar", "allowUndertaker"]
        char_keys = ["Wylder", "Guardian", "Ironeye", "Duchess", "Raider",
                     "Revenant", "Recluse", "Executor", "Scholar", "Undertaker"]
        results: list[dict] = []
        seen: dict[str, int] = {}
        for _, row in full.iterrows():
            eff_id = int(row["ID"])
            if eff_id == 0:
                continue
            name = self.get_effect_name(eff_id).strip()
            if name == "Empty" or name.startswith("Effect "):
                continue

            # For source-overridden names, deep-pool effects get a separate entry
            source = None
            dedup_key = name
            if name in source_override_names and self.is_deep_pool_effect(eff_id):
                source = "deep"
                dedup_key = f"{name}||deep"

            if dedup_key in seen:
                idx = seen[dedup_key]
                if eff_id == int(row["attachTextId"]):
                    # New canonical: demote old canonical ID to alias list
                    results[idx]["alias_ids"].append(results[idx]["id"])
                    results[idx]["id"] = eff_id
                else:
                    results[idx]["alias_ids"].append(eff_id)
                continue
            seen[dedup_key] = len(results)
            allow = {k: bool(row.get(c, 1)) for c, k in zip(char_cols, char_keys)}
            results.append({
                "id": eff_id,
                "name": name,
                "alias_ids": [],
                "compatibility_id": int(row["compatibilityId"]),
                "is_debuff": bool(row.get("isDebuff", 0)),
                "allow_per_character": allow,
                "source": source,
            })
        return results

    # ------------------------------------------------------------------
    # Stacking rules
    # ------------------------------------------------------------------

    def _load_stacking_rules(self) -> None:
        import orjson
        rules_path = self._resources_dir / "json" / "stacking_rules.json"
        self._stacking_cache: dict[int, str] = {}
        self._source_override_names: set[str] = set()
        if not rules_path.exists():
            return
        try:
            rules = orjson.loads(rules_path.read_bytes())
        except Exception:
            return

        def _norm(s: str) -> str:
            return re.sub(r'[\s%]+', ' ', s).strip().lower()

        name_to_type = {_norm(k): v for k, v in rules.items() if not k.startswith("_")}

        # Source overrides: same display name, different stacking for deep vs regular
        raw_overrides = rules.get("_source_overrides", {})
        source_overrides: dict[str, dict[str, str]] = {
            _norm(k): v for k, v in raw_overrides.items()
        }
        self._source_override_names = set(raw_overrides.keys())

        if self.effect_name is None:
            self._load_text()

        def _resolve(eff_id: int, normed: str) -> Optional[str]:
            """Resolve stacking type, checking source overrides first."""
            if normed in source_overrides:
                source_key = "deep" if self.is_deep_pool_effect(eff_id) else "regular"
                stype = source_overrides[normed].get(source_key)
                if stype:
                    return stype
            return name_to_type.get(normed) or name_to_type.get(normed.rsplit("(", 1)[0].strip())

        # Pass 1: direct FMG match
        for _, row in self.effect_name.iterrows():
            eff_id = int(row["id"])
            name = str(row["text"])
            if name == "%null%" or eff_id not in self.effect_params.index:
                continue
            normed = _norm(name)
            stype = _resolve(eff_id, normed)
            if stype:
                self._stacking_cache[eff_id] = stype

        # Pass 2: params resolved via attachTextId
        for eff_id in self.effect_params.index:
            if eff_id in self._stacking_cache or eff_id in (0, -1):
                continue
            name = self.get_effect_name(eff_id)
            if not name or name in ("Empty",) or name.startswith("Effect "):
                continue
            normed = _norm(name)
            stype = _resolve(eff_id, normed)
            if stype:
                self._stacking_cache[eff_id] = stype

    def get_effect_stacking_type(self, effect_id: int) -> str:
        """Return 'stack', 'unique', or 'no_stack' for an effect.

        Default is 'no_stack' (safe fallback for unknown effects).
        Class-specific effects (conflictId=900) use 'unique' so only exact
        duplicates are blocked.
        """
        if not hasattr(self, "_stacking_cache"):
            self._load_stacking_rules()
        result = self._stacking_cache.get(effect_id)
        if not result:
            text_id = self.get_effect_text_id(effect_id)
            if text_id != -1 and text_id != effect_id:
                result = self._stacking_cache.get(text_id)
        result = result or "no_stack"
        if result == "no_stack" and self.get_effect_conflict_id(effect_id) == 900:
            result = "unique"
        return result

    # ------------------------------------------------------------------
    # Effect families (magnitude grouping)
    # ------------------------------------------------------------------

    _MAGNITUDE_RE = re.compile(r'^(.+?)\s+\+(\d+)%?$')

    def _build_effect_families(self) -> None:
        import orjson
        self._effect_families: dict[str, dict] = {}
        self._effect_id_to_family: dict[int, tuple] = {}

        rules_path = self._resources_dir / "json" / "stacking_rules.json"
        if not rules_path.exists():
            return
        try:
            rules = orjson.loads(rules_path.read_bytes())
        except Exception:
            return

        def _norm(s: str) -> str:
            return re.sub(r'[\s%]+', ' ', s).strip().lower()

        # Step 1: parse rules into (base, magnitude) groups
        raw_groups: dict[str, list[tuple[str, int]]] = {}
        for name in rules:
            if name.startswith("_"):
                continue
            clean = name.rstrip('%').rstrip()
            m = self._MAGNITUDE_RE.match(clean)
            base, mag = (m.group(1), int(m.group(2))) if m else (clean, 0)
            raw_groups.setdefault(base, []).append((clean, mag))

        for base, members in raw_groups.items():
            if len(members) >= 2 and any(mag > 0 for _, mag in members):
                members.sort(key=lambda x: x[1])
                self._effect_families[base] = {
                    "members": [{"name": n, "magnitude": mag, "effect_ids": []}
                                for n, mag in members]
                }

        if self.effect_name is None:
            self._load_text()

        # Step 2: discover additional families from FMG names
        existing_norms = {_norm(b) for b in self._effect_families}
        fmg_groups: dict[str, list[tuple[str, int]]] = {}
        for _, row in self.effect_name.iterrows():
            name = re.sub(r'\s+', ' ', str(row["text"])).strip()
            if name == "%null%" or not name:
                continue
            eff_id = int(row["id"])
            if eff_id not in self.effect_params.index:
                continue
            m = self._MAGNITUDE_RE.match(name)
            base, mag = (m.group(1), int(m.group(2))) if m else (name, 0)
            fmg_groups.setdefault(base, []).append((name, mag))

        for base, members in fmg_groups.items():
            if _norm(base) in existing_norms:
                continue
            seen_names: set[str] = set()
            unique = [(n, mag) for n, mag in members if n not in seen_names and not seen_names.add(n)]  # type: ignore[func-returns-value]
            if len(unique) >= 2 and any(mag > 0 for _, mag in unique):
                unique.sort(key=lambda x: x[1])
                self._effect_families[base] = {
                    "members": [{"name": n, "magnitude": mag, "effect_ids": []}
                                for n, mag in unique]
                }

        # Step 3: build normalized name -> family member lookup
        family_name_norm: dict[str, list[tuple[str, int]]] = {}
        for base, fam in self._effect_families.items():
            for idx, member in enumerate(fam["members"]):
                normed = _norm(member["name"])
                family_name_norm.setdefault(normed, []).append((base, idx))

        # Pass 1: direct FMG match
        matched: set[int] = set()
        for _, row in self.effect_name.iterrows():
            eff_id = int(row["id"])
            name = str(row["text"])
            if name == "%null%" or eff_id not in self.effect_params.index:
                continue
            normed = _norm(name)
            hits = family_name_norm.get(normed) or family_name_norm.get(normed.rsplit("(", 1)[0].strip())
            if hits:
                matched.add(eff_id)
                for base, idx in hits:
                    self._effect_families[base]["members"][idx]["effect_ids"].append(eff_id)

        # Pass 2: params resolved via attachTextId
        for eff_id in self.effect_params.index:
            if eff_id in matched or eff_id in (0, -1):
                continue
            name = self.get_effect_name(eff_id)
            if not name or name in ("Empty",) or name.startswith("Effect "):
                continue
            normed = _norm(name)
            hits = family_name_norm.get(normed) or family_name_norm.get(normed.rsplit("(", 1)[0].strip())
            if hits:
                for base, idx in hits:
                    self._effect_families[base]["members"][idx]["effect_ids"].append(eff_id)

        # Remove members with no IDs
        for fam in self._effect_families.values():
            fam["members"] = [m for m in fam["members"] if m["effect_ids"]]

        # Build reverse lookup; prune families with fewer than 2 resolved members
        # (single-member families arise when +1/+2 variants in stacking_rules.json
        # have no corresponding effect params, leaving only the base entry)
        to_remove = []
        for base, fam in self._effect_families.items():
            if len(fam["members"]) < 2:
                to_remove.append(base)
                continue
            total = len(fam["members"])
            for rank, member in enumerate(fam["members"], 1):
                for eid in member["effect_ids"]:
                    self._effect_id_to_family[eid] = (base, rank, total)
        for base in to_remove:
            del self._effect_families[base]

    def _ensure_families(self) -> None:
        if not hasattr(self, "_effect_families"):
            self._build_effect_families()

    def get_effect_family(self, effect_id: int) -> Optional[str]:
        self._ensure_families()
        info = self._effect_id_to_family.get(effect_id)
        if info:
            return info[0]
        text_id = self.get_effect_text_id(effect_id)
        if text_id != -1 and text_id != effect_id:
            info = self._effect_id_to_family.get(text_id)
            return info[0] if info else None
        return None

    def get_family_magnitude_weight(self, effect_id: int, base_weight: int) -> int:
        """Scale weight by rank/total within the effect's family."""
        self._ensure_families()
        info = self._effect_id_to_family.get(effect_id)
        if not info:
            text_id = self.get_effect_text_id(effect_id)
            if text_id != -1 and text_id != effect_id:
                info = self._effect_id_to_family.get(text_id)
        if not info:
            return base_weight
        _, rank, total = info
        return int(base_weight * rank / total)

    def get_family_effect_ids(self, family_name: str) -> set[int]:
        self._ensure_families()
        fam = self._effect_families.get(family_name)
        if not fam:
            return set()
        ids: set[int] = set()
        for member in fam["members"]:
            ids.update(member["effect_ids"])
        return ids

    def get_all_families_list(self) -> list[dict]:
        """All effect families for the build UI."""
        self._ensure_families()
        results = []
        for base, fam in self._effect_families.items():
            ids: set[int] = set()
            for m in fam["members"]:
                ids.update(m["effect_ids"])
            if ids:
                results.append({
                    "name": base,
                    "member_names": [m["name"] for m in fam["members"]],
                    "member_ids": ids,
                })
        return sorted(results, key=lambda x: x["name"])

    # ------------------------------------------------------------------
    # Pool queries
    # ------------------------------------------------------------------

    def get_pool_effects(self, pool_id: int) -> list[int]:
        if pool_id == -1:
            return []
        return (self.effect_table[self.effect_table["ID"] == pool_id]
                ["attachEffectId"].values.tolist())

    def get_pool_rollable_effects(self, pool_id: int) -> list[int]:
        """Effects with non-zero chanceWeight in a pool.

        For deep pools (2000000/2100000/2200000), returns effects rollable in ANY
        of the three deep pools (the game treats them interchangeably).
        """
        if pool_id == -1:
            return []
        deep_pools = {2000000, 2100000, 2200000}
        if pool_id in deep_pools:
            e = self.effect_table[self.effect_table["ID"].isin(deep_pools)]
            return _filter_nonzero_weight(e)["attachEffectId"].unique().tolist()
        e = self.effect_table[self.effect_table["ID"] == pool_id]
        return _filter_nonzero_weight(e)["attachEffectId"].values.tolist()

    def get_pool_effects_strict(self, pool_id: int) -> list[int]:
        """Effects with non-zero chanceWeight in a SPECIFIC pool (no deep-pool merging)."""
        if pool_id == -1:
            return []
        e = self.effect_table[self.effect_table["ID"] == pool_id]
        return _filter_nonzero_weight(e)["attachEffectId"].values.tolist()

    def get_effect_pools(self, effect_id: int) -> list[int]:
        return (self.effect_table[self.effect_table["attachEffectId"] == effect_id]
                ["ID"].values.tolist())

    def get_effect_rollable_pools(self, effect_id: int) -> list[int]:
        rows = self.effect_table[self.effect_table["attachEffectId"] == effect_id]
        return _filter_nonzero_weight(rows)["ID"].values.tolist()

    def is_deep_only_effect(self, effect_id: int) -> bool:
        if effect_id in (-1, 0, 4294967295):
            return False
        deep_pools = {2000000, 2100000, 2200000}
        return all(p in deep_pools or p == effect_id for p in self.get_effect_pools(effect_id))

    def is_deep_pool_effect(self, effect_id: int) -> bool:
        """True if the effect appears in any deep relic pool."""
        if effect_id in (-1, 0, 4294967295):
            return False
        deep_pools = {2000000, 2100000, 2200000}
        return bool(deep_pools & set(self.get_effect_pools(effect_id)))

    def effect_needs_curse(self, effect_id: int) -> bool:
        """True if effect can ONLY roll from pool 2000000 (curse-required pool)."""
        if effect_id in (-1, 0, 4294967295):
            return False
        pools = self.get_effect_rollable_pools(effect_id)
        in_curse_required = False
        in_curse_free = False
        for p in pools:
            if p == effect_id:
                continue
            if p == 2000000:
                in_curse_required = True
            elif p in {2100000, 2200000}:
                in_curse_free = True
        return in_curse_required and not in_curse_free

    def get_adjusted_pool_sequence(self, relic_id: int, effects: list[int]) -> list:
        pool_ids = self.get_relic_pools_seq(relic_id)
        curse_pools = list(pool_ids[3:])
        new_ids = list(pool_ids[:3])
        for eff in effects[:3]:
            new_ids.append(curse_pools.pop(0) if self.effect_needs_curse(eff) else -1)
        return new_ids

    # ------------------------------------------------------------------
    # Character / vessel queries
    # ------------------------------------------------------------------

    def get_character_name(self, character_id: int) -> str:
        return self.npc_name[self.npc_name["id"] == character_id]["text"].values[0]

    def get_vessel_data(self, vessel_id: int) -> Optional[dict]:
        df = self.antique_stand_param
        rows = df[df["ID"] == vessel_id][[
            "goodsId", "heroType",
            "relicSlot1", "relicSlot2", "relicSlot3",
            "deepRelicSlot1", "deepRelicSlot2", "deepRelicSlot3",
            "unlockFlag",
        ]]
        if rows.empty:
            return None
        hero_type   = int(rows["heroType"].values[0])
        unlock_flag = int(rows["unlockFlag"].values[0])
        goods_id    = rows["goodsId"].values[0]
        vessel_name_rows = self.vessel_names[self.vessel_names["id"] == goods_id]["text"].values
        if len(vessel_name_rows) == 0:
            return None
        return {
            "Name":       vessel_name_rows[0],
            "Character":  (self.get_character_name(CHARACTER_NAME_ID[hero_type - 1])
                           if hero_type != 11 else "All"),
            "Colors": (
                COLOR_MAP[rows["relicSlot1"].values[0]],
                COLOR_MAP[rows["relicSlot2"].values[0]],
                COLOR_MAP[rows["relicSlot3"].values[0]],
                COLOR_MAP[rows["deepRelicSlot1"].values[0]],
                COLOR_MAP[rows["deepRelicSlot2"].values[0]],
                COLOR_MAP[rows["deepRelicSlot3"].values[0]],
            ),
            "unlockFlag": unlock_flag,
            "hero_type":  hero_type,
        }

    def get_all_vessels_for_hero(self, hero_type: int) -> list[dict]:
        """All vessels available for a hero (hero-specific + shared heroType=11)."""
        df = self.antique_stand_param
        matching = df[((df["heroType"] == hero_type) | (df["heroType"] == 11)) &
                      (df["disableParam_NT"] == 0)]
        results = []
        for _, row in matching.iterrows():
            vessel_id = int(row["ID"])
            try:
                vd = self.get_vessel_data(vessel_id)
                if vd:
                    results.append({"vessel_id": vessel_id, **vd})
            except Exception:
                continue
        return results
