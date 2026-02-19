import re

import pandas as pd
import pathlib
from typing import Optional, Union
import locale

from globals import COLOR_MAP, LANGUAGE_MAP, CHARACTER_NAME_ID, CHARACTER_NAMES, RELIC_GROUPS


def get_system_language():
    lang = None

    try:
        lang, _ = locale.getdefaultlocale()
    except Exception:
        return "en_US"

    if lang:
        normalized = locale.normalize(lang)
        clean_lang = normalized.split('.')[0]
        clean_lang = clean_lang.replace('-', '_')
        if clean_lang in LANGUAGE_MAP:
            return clean_lang
        else:
            return "en_US"
    return "en_US"


def df_filter_zero_chanceWeight(effects: pd.DataFrame) -> pd.DataFrame:
    """
    Filter effects DataFrame to include only those with non-zero FINAL chanceWeight.
    chanceWeight_dlc explains from Smithbox(unpacking tool):
    The DLC new Weighting to apply during the roll.
    -1 will use base roll weight(chanceWeight).

    Args:
        effects (pd.DataFrame): DataFrame import from AttachEffectTableParam.csv .\n
            Can be filtered before calling this function,\n
            but must have 'chanceWeight' and 'chanceWeight_dlc' columns.

    Returns:
        DataFrame:
            Filtered DataFrame with effects that have non-zero chanceWeight
    """
    _effs = effects.copy()
    _effs = _effs[(_effs["chanceWeight_dlc"] > 0) |
                  ((_effs["chanceWeight"] != 0) & (_effs["chanceWeight_dlc"] == -1))]
    return _effs


class SourceDataHandler:
    WORKING_DIR = pathlib.Path(__file__).parent.resolve()
    PARAM_DIR = pathlib.Path(WORKING_DIR / "Resources/Param")
    TEXT_DIR = pathlib.Path(WORKING_DIR / "Resources/Text")
    RELIC_TEXT_FILE_NAME = ["AntiqueName.fmg.xml", "AntiqueName_dlc01.fmg.xml"]
    EFFECT_NAME_FILE_NAMES = [
        "AttachEffectName.fmg.xml",
        "AttachEffectName_dlc01.fmg.xml",
    ]
    NPC_NAME_FILE_NAMES = [
        "NpcName.fmg.xml",
        "NpcName_dlc01.fmg.xml",
    ]
    GOODS_NAME_FILE_NAMES = [
        "GoodsName.fmg.xml",
        "GoodsName_dlc01.fmg.xml",
    ]
    character_names = CHARACTER_NAMES

    def __init__(self, language: str = "en_US"):
        self.effect_params = \
            pd.read_csv(self.PARAM_DIR / "AttachEffectParam.csv")
        self.effect_params: pd.DataFrame = self.effect_params[
            ["ID", "compatibilityId", "attachTextId", "overrideEffectId"]
        ]
        self.effect_params.set_index("ID", inplace=True)

        self.effect_table = \
            pd.read_csv(self.PARAM_DIR / "AttachEffectTableParam.csv")
        self.effect_table: pd.DataFrame = \
            self.effect_table[["ID", "attachEffectId", "chanceWeight", "chanceWeight_dlc"]]

        self.relic_table = \
            pd.read_csv(self.PARAM_DIR / "EquipParamAntique.csv")
        self.relic_table: pd.DataFrame = self.relic_table[
            [
                "ID",
                "relicColor",
                "attachEffectTableId_1",
                "attachEffectTableId_2",
                "attachEffectTableId_3",
                "attachEffectTableId_curse1",
                "attachEffectTableId_curse2",
                "attachEffectTableId_curse3",
            ]
        ]
        self.relic_table.set_index("ID", inplace=True)

        self.antique_stand_param: pd.DataFrame = \
            pd.read_csv(self.PARAM_DIR / "AntiqueStandParam.csv")

        self.relic_name: Optional[pd.DataFrame] = None
        self.effect_name: Optional[pd.DataFrame] = None
        self.npc_name: Optional[pd.DataFrame] = None
        # Track which relic IDs are from 1.03 patch (Scene relics)
        self._scene_relic_ids: set = set()
        self.vessel_names: Optional[pd.DataFrame] = None
        self._load_text(language)

    def _load_text(self, language: str = "en_US"):
        support_languages = LANGUAGE_MAP.keys()
        _lng = language
        if language not in support_languages:
            _lng = "en_US"
        # Deal with Relic text
        # Read all Relic xml from language subfolder
        # Track which IDs come from _dlc01 file (1.03 patch / Scene relics)
        _relic_names: Optional[pd.DataFrame] = None
        self._scene_relic_ids = set()
        for file_name in SourceDataHandler.RELIC_TEXT_FILE_NAME:
            _df = pd.read_xml(
                SourceDataHandler.TEXT_DIR / _lng / file_name,
                xpath="/fmg/entries/text"
            )
            # Track IDs from dlc01 file as Scene relics (1.03 patch)
            if "_dlc01" in file_name:
                valid_ids = _df[_df['text'] != '%null%']['id'].tolist()
                self._scene_relic_ids.update(valid_ids)
            if _relic_names is None:
                _relic_names = _df
            else:
                _relic_names = pd.concat([_relic_names, _df])

        # Deal with Effect text
        # Read all Effect xml from language subfolder
        _effect_names: Optional[pd.DataFrame] = None
        for file_name in SourceDataHandler.EFFECT_NAME_FILE_NAMES:
            _df = pd.read_xml(
                SourceDataHandler.TEXT_DIR / _lng / file_name,
                xpath="/fmg/entries/text"
            )
            if _effect_names is None:
                _effect_names = _df
            else:
                _effect_names = pd.concat([_effect_names, _df])

        # Deal with NPC text
        # Read all NPC xml from language subfolder
        _npc_names: Optional[pd.DataFrame] = None
        for file_name in SourceDataHandler.NPC_NAME_FILE_NAMES:
            _df = pd.read_xml(
                SourceDataHandler.TEXT_DIR / _lng / file_name,
                xpath="/fmg/entries/text"
            )
            if _npc_names is None:
                _npc_names = _df
            else:
                _npc_names = pd.concat([_npc_names, _df])

        self.character_names.clear()
        for id in CHARACTER_NAME_ID:
            _name = _npc_names[_npc_names["id"] == id]["text"].to_list()[0]
            self.character_names.append(_name)

        # Deal with Goods Names
        # Read all Goods xml from language subfolder
        _goods_names: Optional[pd.DataFrame] = None
        for file_name in SourceDataHandler.GOODS_NAME_FILE_NAMES:
            _df = pd.read_xml(
                SourceDataHandler.TEXT_DIR / _lng / file_name,
                xpath="/fmg/entries/text"
            )
            if _goods_names is None:
                _goods_names = _df
            else:
                _goods_names = pd.concat([_goods_names, _df])

        self.vessel_names = _goods_names[(9600 <= _goods_names["id"]) &
                                         (_goods_names["id"] <= 9956) &
                                         (_goods_names["text"] != "%null%")]
        self.npc_name = _npc_names
        self.relic_name = _relic_names
        self.effect_name = _effect_names

    def reload_text(self, language: str = "en_US"):
        try:
            self._load_text(language=language)
            return True
        except FileNotFoundError:
            self._load_text()
            return False
        except KeyError:
            self._load_text()
            return False

    def get_support_languages_name(self):
        return LANGUAGE_MAP.values()

    def get_support_languages_code(self):
        return LANGUAGE_MAP.keys()

    def get_support_languages(self):
        return LANGUAGE_MAP

    def get_relic_origin_structure(self):
        if self.relic_name is None:
            self._load_text()
        _copy_df = self.relic_name.copy()
        _copy_df.set_index("id", inplace=True)
        _copy_df.rename(columns={"text": "name"}, inplace=True)
        _result = {}
        for index, row in self.relic_table.iterrows():
            try:
                _name_matches = \
                    _copy_df[_copy_df.index == index]["name"].values
                _color_matches = \
                    self.relic_table[self.relic_table.index == index][
                        "relicColor"].values
                first_name_val = \
                    _name_matches[0] if len(_name_matches) > 0 else "Unset"
                first_color_val = COLOR_MAP[int(_color_matches[0])] if len(_color_matches) > 0 else "Red"
                _result[str(index)] = {
                    "name": str(first_name_val),
                    "color": first_color_val,
                }
            except KeyError:
                _result[str(index)] = {"name": "Unset", "color": "Red"}
        return _result

    def get_relic_datas(self):
        if self.relic_name is None:
            self._load_text()
        _name_map = self.relic_name.copy()
        _name_map.reset_index(inplace=True, drop=True)
        _name_map.rename(columns={"text": "name"}, inplace=True)
        _result = self.relic_table.copy()
        _result.reset_index(inplace=True)
        _result = pd.merge(
            _result,
            _name_map,
            how="left",
            left_on="ID",
            right_on="id",
        )
        _result.drop(columns=["id"], inplace=True)
        _result.set_index("ID", inplace=True)
        return _result

    def get_relic_color(self, relic_id: int):
        color_id = self.relic_table.loc[relic_id, "relicColor"]
        return COLOR_MAP[color_id]

    def cvrt_filtered_relic_origin_structure(self,
                                             relic_dataframe: pd.DataFrame):
        if self.relic_name is None:
            self._load_text()
        _copy_df = self.relic_name.copy()
        _copy_df.set_index("id", inplace=True)
        _copy_df.rename(columns={"text": "name"}, inplace=True)
        _result = {}
        for index, row in relic_dataframe.iterrows():
            try:
                _name_matches = \
                    _copy_df[_copy_df.index == index]["name"].values
                _color_matches = \
                    relic_dataframe[relic_dataframe.index == index][
                        "relicColor"].values
                first_name_val = \
                    _name_matches[0] if len(_name_matches) > 0 else "Unset"
                first_color_val = COLOR_MAP[int(_color_matches[0])] if len(_color_matches) > 0 else "Red"
                _result[str(index)] = {
                    "name": str(first_name_val),
                    "color": first_color_val,
                }
            except KeyError:
                _result[str(index)] = {"name": "Unset", "color": "Red"}
        return _result

    def get_effect_datas(self):
        if self.effect_name is None:
            self._load_text()
        _name_map = self.effect_name.copy()
        _name_map.reset_index(inplace=True, drop=True)
        _name_map.rename(columns={"text": "name"}, inplace=True)
        _result = self.effect_params.copy()
        _result.reset_index(inplace=True)
        _result = pd.merge(
            _result,
            _name_map,
            how="left",
            left_on="attachTextId",
            right_on="id",
        )
        _result.drop(columns=["id"], inplace=True)
        _result.set_index("ID", inplace=True)
        _result.fillna({"name": "Unknown"}, inplace=True)
        return _result

    def get_effect_origin_structure(self):
        if self.effect_name is None:
            self._load_text()
        _copy_df = self.effect_name.copy()
        _copy_df.set_index("id", inplace=True)
        _reslut = {"4294967295": {"name": "Empty"}}
        for index, row in self.effect_params.iterrows():
            try:
                _attachTextId = self.effect_params.loc[index, "attachTextId"]
                matches = \
                    _copy_df[_copy_df.index == _attachTextId]["text"].values
                first_val = matches[0] if len(matches) > 0 else "Unknown"
                _reslut[str(index)] = {"name": str(first_val)}
            except KeyError:
                _reslut[str(index)] = {"name": "Unknown"}
        return _reslut

    def cvrt_filtered_effect_origin_structure(self,
                                              effect_dataframe: pd.DataFrame):
        if self.effect_name is None:
            self._load_text()
        _copy_df = self.effect_name.copy()
        _copy_df.set_index("id", inplace=True)
        _reslut = {}
        for index, row in effect_dataframe.iterrows():
            try:
                _attachTextId = effect_dataframe.loc[index, "attachTextId"]
                matches = \
                    _copy_df[_copy_df.index == _attachTextId]["text"].values
                first_val = matches[0] if len(matches) > 0 else "Unknown"
                _reslut[str(index)] = {"name": str(first_val)}
            except KeyError:
                _reslut[str(index)] = {"name": "Unknown"}
        if len(_reslut) == 0:
            _reslut = {"4294967295": {"name": "Empty"}}
        return _reslut

    def get_relic_pools_seq(self, relic_id: int):
        _pool_ids = self.relic_table.loc[relic_id,
                                         ["attachEffectTableId_1",
                                          "attachEffectTableId_2",
                                          "attachEffectTableId_3",
                                          "attachEffectTableId_curse1",
                                          "attachEffectTableId_curse2",
                                          "attachEffectTableId_curse3"]]
        return _pool_ids.values.tolist()

    def is_scene_relic(self, relic_id: int) -> bool:
        """Check if a relic is a Scene relic (added in patch 1.03).

        Scene relics have different effect pools than original relics,
        which is why certain effects can only be found on Scene relics
        and vice versa.

        Returns:
            True if the relic is a Scene relic (1.03+), False otherwise
        """
        return relic_id in self._scene_relic_ids

    def get_relic_type_info(self, relic_id: int) -> tuple:
        """Get relic type information for display purposes.

        Returns:
            Tuple of (type_name, description, color_hex)
            - type_name: "Scene" or "Original"
            - description: Brief explanation of what this means
            - color_hex: Color for display
        """
        if self.is_scene_relic(relic_id):
            return (
                "Scene Relic (1.03+)",
                "Has unique effect pools not found on original relics",
                "#9966CC"  # Purple for Scene relics
            )
        else:
            return (
                "Original Relic",
                "Has effect pools from base game release",
                "#666666"  # Gray for original relics
            )

    def get_effect_text_id(self, effect_id: int) -> int:
        """Return the attachTextId (canonical text ID) for an effect.

        Many variant effects share the same attachTextId as the base effect,
        meaning they are functionally identical. Returns -1 if not found.
        """
        try:
            if effect_id in [-1, 0, 4294967295]:
                return -1
            if effect_id in self.effect_params.index:
                return int(self.effect_params.loc[effect_id, "attachTextId"])
        except (KeyError, ValueError):
            pass
        return -1

    def get_effect_conflict_id(self, effect_id: int):
        try:
            if effect_id == -1 or effect_id == 4294967295:
                return -1
            _conflict_id = self.effect_params.loc[effect_id, "compatibilityId"]
            return _conflict_id
        except KeyError:
            return -1

    def get_sort_id(self, effect_id: int):
        try:
            _sort_id = self.effect_params.loc[effect_id, "overrideEffectId"]
            return _sort_id
        except KeyError:
            pass
        return -1

    def get_effect_name(self, effect_id: int) -> str:
        """Get the name of an effect by its ID."""
        if effect_id in [-1, 0, 4294967295]:
            return "Empty"
        if self.effect_name is None:
            self._load_text()
        try:
            # Try direct ID match first (works when param ID == text ID)
            row = self.effect_name[self.effect_name["id"] == effect_id]
            if not row.empty:
                return row["text"].values[0]
            # Fall back to attachTextId lookup (some effects have a
            # different param ID than their text ID)
            if effect_id in self.effect_params.index:
                text_id = int(self.effect_params.loc[effect_id, "attachTextId"])
                if text_id != -1:
                    row = self.effect_name[self.effect_name["id"] == text_id]
                    if not row.empty:
                        return row["text"].values[0]
        except Exception:
            pass
        return f"Effect {effect_id}"

    def _load_stacking_rules(self):
        """Load stacking rules and build effect_id -> stacking_type cache."""
        import orjson
        rules_path = self.WORKING_DIR / "Resources" / "Json" / "stacking_rules.json"
        self._stacking_cache: dict[int, str] = {}
        if not rules_path.exists():
            return
        try:
            rules = orjson.loads(rules_path.read_bytes())
        except Exception:
            return

        # Build name -> stacking_type lookup (case-insensitive)
        name_to_type: dict[str, str] = {}
        for name, stype in rules.items():
            if name.startswith("_"):
                continue
            name_to_type[name.lower()] = stype

        # Resolve effect names to IDs
        if self.effect_name is None:
            self._load_text()
        for _, row in self.effect_name.iterrows():
            eff_id = int(row["id"])
            eff_name = str(row["text"])
            if eff_name == "%null%":
                continue
            # Only cache effects that actually exist as game params
            if eff_id not in self.effect_params.index:
                continue
            # Try exact match (case-insensitive)
            lower_name = eff_name.lower()
            if lower_name in name_to_type:
                self._stacking_cache[eff_id] = name_to_type[lower_name]
                continue
            # Strip parenthetical suffix and try again
            # e.g. "Stamina recovers with each successful attack +1 (Night of the Beast)"
            stripped = lower_name.rsplit("(", 1)[0].strip()
            if stripped in name_to_type:
                self._stacking_cache[eff_id] = name_to_type[stripped]

    def get_effect_stacking_type(self, effect_id: int) -> str:
        """Return stacking type for an effect: 'stack', 'unique', or 'no_stack'.

        - 'stack': Fully additive, multiple copies all provide benefit.
        - 'unique': Doesn't self-stack, but stacks with different variants.
        - 'no_stack': Only one instance provides benefit, blocks group.
        Falls back to attachTextId for variant effects.
        Default: 'no_stack' for unknown effects (safe fallback).
        """
        if not hasattr(self, '_stacking_cache'):
            self._load_stacking_rules()
        result = self._stacking_cache.get(effect_id)
        if result:
            return result
        # Variant effects share attachTextId with the base — use its rules
        text_id = self.get_effect_text_id(effect_id)
        if text_id != -1 and text_id != effect_id:
            result = self._stacking_cache.get(text_id)
            if result:
                return result
        return "no_stack"

    # ---- Effect Families (magnitude grouping) ----

    _MAGNITUDE_RE = re.compile(r'^(.+?)\s+\+(\d+)%?$')

    def _build_effect_families(self):
        """Build effect family groupings from stacking_rules.json.

        Groups effects like "Vigor +1/+2/+3" into a family "Vigor",
        ordered by magnitude so higher variants score higher.
        """
        import orjson
        self._effect_families: dict[str, dict] = {}
        self._effect_id_to_family: dict[int, tuple] = {}

        rules_path = self.WORKING_DIR / "Resources" / "Json" / "stacking_rules.json"
        if not rules_path.exists():
            return
        try:
            rules = orjson.loads(rules_path.read_bytes())
        except Exception:
            return

        # Step 1: Parse effect names into (base_name, magnitude) groups
        raw_groups: dict[str, list[tuple[str, int]]] = {}
        for name in rules:
            if name.startswith("_"):
                continue
            m = self._MAGNITUDE_RE.match(name)
            if m:
                base = m.group(1)
                mag = int(m.group(2))
            else:
                base = name
                mag = 0
            raw_groups.setdefault(base, []).append((name, mag))

        # Step 2: Keep only groups with 2+ members (real families)
        # and where at least one member has magnitude > 0
        for base, members in raw_groups.items():
            if len(members) < 2:
                continue
            has_variant = any(mag > 0 for _, mag in members)
            if not has_variant:
                continue
            # Sort by magnitude
            members.sort(key=lambda x: x[1])
            self._effect_families[base] = {
                "members": [{"name": n, "magnitude": mag, "effect_ids": []}
                            for n, mag in members],
            }

        # Step 3: Map effect names to IDs
        if self.effect_name is None:
            self._load_text()

        # Build lowered name -> list of (family_base, member_index) lookups
        # Also register variants with % stripped, since stacking_rules.json
        # uses names like "Fire Attack Power Up +3%" but XML uses "+3"
        family_name_lower: dict[str, list[tuple[str, int]]] = {}
        for base, fam in self._effect_families.items():
            for idx, member in enumerate(fam["members"]):
                lower = member["name"].lower()
                family_name_lower.setdefault(lower, []).append((base, idx))
                # Also try without trailing %
                if lower.endswith('%'):
                    stripped = lower.rstrip('%')
                    family_name_lower.setdefault(stripped, []).append((base, idx))

        for _, row in self.effect_name.iterrows():
            eff_id = int(row["id"])
            eff_name = str(row["text"])
            if eff_name == "%null%":
                continue
            # Only include effects that actually exist as game params
            # (some FMG text entries are phantoms with no param backing)
            if eff_id not in self.effect_params.index:
                continue
            lower = eff_name.lower()
            # Try exact match
            matches = family_name_lower.get(lower)
            if not matches:
                # Strip parenthetical suffix
                stripped = lower.rsplit("(", 1)[0].strip()
                matches = family_name_lower.get(stripped)
            if matches:
                for base, idx in matches:
                    self._effect_families[base]["members"][idx]["effect_ids"].append(eff_id)

        # Step 3.5: Remove members with no valid effect IDs
        for base, fam in self._effect_families.items():
            fam["members"] = [m for m in fam["members"] if m["effect_ids"]]

        # Step 4: Build reverse lookup and clean up empty families
        to_remove = []
        for base, fam in self._effect_families.items():
            total = len(fam["members"])
            has_ids = False
            for rank, member in enumerate(fam["members"], 1):
                if member["effect_ids"]:
                    has_ids = True
                for eid in member["effect_ids"]:
                    self._effect_id_to_family[eid] = (base, rank, total)
            if not has_ids:
                to_remove.append(base)
        for base in to_remove:
            del self._effect_families[base]

    def _ensure_families(self):
        if not hasattr(self, '_effect_families'):
            self._build_effect_families()

    def get_effect_family(self, effect_id: int) -> Optional[str]:
        """Return the family base name for an effect, or None.

        Falls back to attachTextId for variant effects.
        """
        self._ensure_families()
        info = self._effect_id_to_family.get(effect_id)
        if info:
            return info[0]
        # Variant effects: try canonical text ID
        text_id = self.get_effect_text_id(effect_id)
        if text_id != -1 and text_id != effect_id:
            info = self._effect_id_to_family.get(text_id)
            return info[0] if info else None
        return None

    def get_family_magnitude_weight(self, effect_id: int, base_weight: int) -> int:
        """Return magnitude-scaled weight for an effect within its family.

        Weight = base_weight * rank / total_members
        (rank is 1-indexed position in ascending magnitude order)
        Falls back to attachTextId for variant effects.
        """
        self._ensure_families()
        info = self._effect_id_to_family.get(effect_id)
        if not info:
            # Variant effects: try canonical text ID
            text_id = self.get_effect_text_id(effect_id)
            if text_id != -1 and text_id != effect_id:
                info = self._effect_id_to_family.get(text_id)
        if not info:
            return base_weight
        _, rank, total = info
        return int(base_weight * rank / total)

    def get_family_effect_ids(self, family_name: str) -> set:
        """Get all effect IDs that belong to a family."""
        self._ensure_families()
        fam = self._effect_families.get(family_name)
        if not fam:
            return set()
        ids = set()
        for member in fam["members"]:
            ids.update(member["effect_ids"])
        return ids

    def get_all_families_list(self) -> list[dict]:
        """Get all effect families for the search dialog.

        Returns list of dicts:
            name: family base name (e.g. "Vigor")
            member_names: list of member display names
            member_ids: set of all effect IDs in family
        """
        self._ensure_families()
        results = []
        for base, fam in self._effect_families.items():
            member_names = [m["name"] for m in fam["members"]]
            member_ids = set()
            for m in fam["members"]:
                member_ids.update(m["effect_ids"])
            if member_ids:
                results.append({
                    "name": base,
                    "member_names": member_names,
                    "member_ids": member_ids,
                })
        results.sort(key=lambda x: x["name"])
        return results

    def get_pool_effects(self, pool_id: int):
        if pool_id == -1:
            return []
        _effects = self.effect_table[self.effect_table["ID"] == pool_id]
        _effects = _effects["attachEffectId"].values.tolist()
        return _effects

    def get_pool_rollable_effects(self, pool_id: int):
        """Get effects that can actually roll in a pool (chanceWeight != 0).

        Effects with weight -65536 are disabled (cannot roll).
        Effects with weight 0 are class-specific effects that cannot naturally roll.
        Other weights (including other negative values) are valid rollable weights.

        For deep pools (2000000, 2100000, 2200000), returns effects that have
        rollable weight in ANY of the three deep pools, since the game appears
        to allow effects to roll on any deep relic if they're valid in any deep pool.
        """
        if pool_id == -1:
            return []

        # Deep pools are interchangeable - effect valid in any deep pool is valid for all
        deep_pools = {2000000, 2100000, 2200000}
        if pool_id in deep_pools:
            # Get effects with rollable weight in ANY deep pool
            _effects = self.effect_table[self.effect_table["ID"].isin(deep_pools)]
            _effects = df_filter_zero_chanceWeight(_effects)
            return _effects["attachEffectId"].unique().tolist()

        # For non-deep pools, check the specific pool
        _effects = self.effect_table[self.effect_table["ID"] == pool_id]
        # Filter out disabled (-65536) and zero-weight effects
        _effects = df_filter_zero_chanceWeight(_effects)
        return _effects["attachEffectId"].values.tolist()

    def get_pool_effects_strict(self, pool_id: int):
        """Get effects that can roll in a SPECIFIC pool (chanceWeight != 0).

        Unlike get_pool_rollable_effects(), this does NOT combine deep pools.
        Use this for strict validation to detect effects that are valid in some
        deep pool but not in the specific pool assigned to a relic.
        """
        if pool_id == -1:
            return []
        _effects = self.effect_table[self.effect_table["ID"] == pool_id]
        _effects = df_filter_zero_chanceWeight(_effects)
        return _effects["attachEffectId"].values.tolist()

    def get_effect_pools(self, effect_id: int):
        """Get all pool IDs that contain a specific effect."""
        _pools = self.effect_table[self.effect_table["attachEffectId"] == effect_id]
        return _pools["ID"].values.tolist()

    def get_effect_rollable_pools(self, effect_id: int):
        """Get all pool IDs where this effect can actually roll (chanceWeight != 0)."""
        _rows = self.effect_table[self.effect_table["attachEffectId"] == effect_id]
        # Filter out rows where chanceWeight is 0 (cannot roll)
        _rollable = df_filter_zero_chanceWeight(_rows)
        return _rollable["ID"].values.tolist()

    def is_deep_only_effect(self, effect_id: int):
        """Check if an effect only exists in deep relic pools (2000000, 2100000, 2200000)
        plus its own dedicated pool (effect_id == pool_id).
        These effects require curses when used on multi-effect relics."""
        if effect_id in [-1, 0, 4294967295]:
            return False
        pools = self.get_effect_pools(effect_id)
        deep_pools = {2000000, 2100000, 2200000}
        for pool in pools:
            # If pool is not a deep pool and not the effect's dedicated pool, it's not deep-only
            if pool not in deep_pools and pool != effect_id:
                return False
        return True

    def effect_needs_curse(self, effect_id: int) -> bool:
        """Check if an effect REQUIRES a curse.

        An effect needs a curse if it can ONLY roll from pool 2000000 (3-effect relics)
        and NOT from pools 2100000 or 2200000 (single-effect relics with no curse).

        We check rollable pools (weight != -65536) because an effect may be listed
        in a pool but with weight -65536 meaning it can't actually roll there.
        """
        if effect_id in [-1, 0, 4294967295]:
            return False

        # Get pools where this effect can actually roll
        pools = self.get_effect_rollable_pools(effect_id)

        # Pool 2000000 = 3-effect relics (always have curse slots)
        # Pools 2100000, 2200000 = single-effect relics (no curse slots)
        curse_required_pool = 2000000
        curse_free_pools = {2100000, 2200000}

        in_curse_required_pool = False
        in_curse_free_pool = False

        for pool in pools:
            if pool == effect_id:
                # Skip dedicated pool (effect's own pool)
                continue
            if pool == curse_required_pool:
                in_curse_required_pool = True
            elif pool in curse_free_pools:
                in_curse_free_pool = True

        # Effect needs curse only if it can roll from pool 2000000
        # AND cannot roll from any curse-free pool (2100000 or 2200000)
        return in_curse_required_pool and not in_curse_free_pool

    def get_adjusted_pool_sequence(self, relic_id: int,
                                   effects: list[int]):
        """
        Get adjusted pool sequence for a relic based on its effects.
        For each of the first three effects, check if it requires a curse.
        If it does, assign the next available curse pool ID.
        If it doesn't, assign -1.
        """
        effs = effects[:3]
        pool_ids = self.get_relic_pools_seq(relic_id)
        curse_pools = pool_ids[3:]
        new_pool_ids = pool_ids[:3]
        for i in range(3):
            if self.effect_needs_curse(effs[i]):
                new_pool_ids.append(curse_pools.pop(0))
            else:
                new_pool_ids.append(-1)
        return new_pool_ids

    def get_relic_slot_count(self, relic_id: int) -> tuple[int, int]:
        pool_seq: list = self.get_relic_pools_seq(relic_id)
        effect_slot = pool_seq[:3]
        curse_slot = pool_seq[3:]
        return 3-effect_slot.count(-1), 3-curse_slot.count(-1)

    def get_character_name(self, character_id: int):
        return self.npc_name[self.npc_name["id"] == character_id]["text"].values[0]

    def get_vessel_data(self, vessel_id: int):
        """
        Get vessel data by vessel ID.
        
        :param vessel_id: vessel ID to get data for
        :type vessel_id: int
        :return: Vessel data as a dictionary
        :rtype: dict
            keys: Name, Character, Colors, unlockFlag, hero_type
        """
        if self.antique_stand_param is None:
            return None
        _vessel_data = self.antique_stand_param[self.antique_stand_param["ID"] == vessel_id][
            ["goodsId", "heroType",
             "relicSlot1", "relicSlot2", "relicSlot3",
             "deepRelicSlot1", "deepRelicSlot2", "deepRelicSlot3",
             "unlockFlag"]
        ]
        # hero type start at 1, and 11 means ALL
        _hero_type = int(_vessel_data["heroType"].values[0])
        _unlock_flag = int(_vessel_data["unlockFlag"].values[0])
        _result = {"Name": self.vessel_names[self.vessel_names["id"] == _vessel_data["goodsId"].values[0]]["text"].values[0],
                   "Character": self.get_character_name(CHARACTER_NAME_ID[_hero_type-1]) if _hero_type != 11 else "All",
                   "Colors": (
                        COLOR_MAP[_vessel_data["relicSlot1"].values[0]],
                        COLOR_MAP[_vessel_data["relicSlot2"].values[0]],
                        COLOR_MAP[_vessel_data["relicSlot3"].values[0]],
                        COLOR_MAP[_vessel_data["deepRelicSlot1"].values[0]],
                        COLOR_MAP[_vessel_data["deepRelicSlot2"].values[0]],
                        COLOR_MAP[_vessel_data["deepRelicSlot3"].values[0]]
                        ),
                   "unlockFlag": _unlock_flag,
                   "hero_type": _hero_type
                   }
        return _result

    def get_filtered_relics_df(self, color: Union[int, str] = None,
                               deep: Optional[bool] = None,
                               effect_slot: Optional[int] = None,
                               curse_slot: Optional[int] = None):
        result_df: pd.DataFrame = self.relic_table.copy()
        result_df.reset_index(inplace=True)
        safe_range = self.get_safe_relic_ids()
        result_df = result_df[result_df["ID"].isin(safe_range)]
        if color is not None:
            color_id = 0
            if type(color) is str:
                color_id = COLOR_MAP.index(color)
            else:
                color_id = color
            result_df = result_df[result_df["relicColor"] == color_id]
        if deep is not None:
            if deep:
                result_df = result_df[result_df["ID"].apply(self.is_deep_relic)]
            else:
                result_df = result_df[~result_df["ID"].apply(self.is_deep_relic)]
        if effect_slot is not None:
            result_df = result_df[result_df["ID"].apply(
                lambda x: self.get_relic_slot_count(x)[0] == effect_slot)]
        if curse_slot is not None:
            result_df = result_df[result_df["ID"].apply(
                lambda x: self.get_relic_slot_count(x)[1] == curse_slot)]
        return result_df

    @staticmethod
    def get_safe_relic_ids():
        range_names = ["store_102", "store_103", "reward_0",
                       "reward_1", "reward_2", "reward_3",
                       "reward_4", "reward_5", "reward_6", "reward_7",
                       "reward_8", "reward_9", "deep_102", "deep_103"]
        safe_relic_ids = []
        for group_name, group_range in RELIC_GROUPS.items():
            if group_name in range_names:
                safe_relic_ids.extend(range(group_range[0], group_range[1] + 1))
        return safe_relic_ids

    @staticmethod
    def is_deep_relic(relic_id: int):
        deep_range_1 = range(RELIC_GROUPS['deep_102'][0],
                             RELIC_GROUPS['deep_102'][1] + 1)
        deep_range_2 = range(RELIC_GROUPS['deep_103'][0],
                             RELIC_GROUPS['deep_103'][1] + 1)
        return relic_id in deep_range_1 or relic_id in deep_range_2


    def get_all_effects_list(self) -> list[dict]:
        """Get all effects with metadata for optimizer UI.

        Returns list of dicts with keys:
            id, name, compatibility_id, is_debuff, allow_per_character
        """
        # Read full effect param for additional columns
        full_params = pd.read_csv(self.PARAM_DIR / "AttachEffectParam.csv")

        character_allow_cols = [
            "allowWylder", "allowGuardian", "allowIroneye", "allowDuchess",
            "allowRaider", "allowRevenant", "allowRecluse", "allowExecutor",
            "allowScholar", "allowUndertaker"
        ]
        character_keys = [
            "Wylder", "Guardian", "Ironeye", "Duchess", "Raider",
            "Revenant", "Recluse", "Executor", "Scholar", "Undertaker"
        ]

        results = []
        for _, row in full_params.iterrows():
            effect_id = int(row["ID"])
            if effect_id == 0:
                continue

            name = self.get_effect_name(effect_id)
            if name == "Empty" or name.startswith("Effect "):
                continue

            compat_id = int(row["compatibilityId"])
            is_debuff = bool(row.get("isDebuff", 0))

            allow = {}
            for col, key in zip(character_allow_cols, character_keys):
                allow[key] = bool(row.get(col, 1))

            results.append({
                "id": effect_id,
                "name": name,
                "compatibility_id": compat_id,
                "is_debuff": is_debuff,
                "allow_per_character": allow,
            })

        return results

    def get_all_vessels_for_hero(self, hero_type: int) -> list[dict]:
        """Get all vessels available for a specific hero.

        Args:
            hero_type: 1-10 for specific heroes

        Returns list of dicts with keys:
            vessel_id, Name, Character, Colors (6-tuple), unlockFlag, hero_type
        """
        if self.antique_stand_param is None:
            return []

        # Filter to hero-specific vessels + shared vessels (heroType=11)
        df = self.antique_stand_param
        matching = df[(df["heroType"] == hero_type) | (df["heroType"] == 11)]
        # Exclude disabled vessels
        matching = matching[matching["disableParam_NT"] == 0]

        results = []
        for _, row in matching.iterrows():
            vessel_id = int(row["ID"])
            try:
                vessel_data = self.get_vessel_data(vessel_id)
                results.append({
                    "vessel_id": vessel_id,
                    "Name": vessel_data["Name"],
                    "Character": vessel_data["Character"],
                    "Colors": vessel_data["Colors"],
                    "unlockFlag": vessel_data["unlockFlag"],
                    "hero_type": vessel_data["hero_type"],
                })
            except Exception:
                continue

        return results


if __name__ == "__main__":
    source_data_handler = SourceDataHandler("zh_TW")
    t = source_data_handler.get_vessel_data(1000)
    print(t)
