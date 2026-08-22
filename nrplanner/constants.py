"""Game constants — no mutable state."""

# Item type flags (upper 4 bits of gaitem_handle)
ITEM_TYPE_WEAPON = 0x80000000
ITEM_TYPE_ARMOR  = 0x90000000
ITEM_TYPE_RELIC  = 0xC0000000

# Sentinel value for empty effect slots in save data
EMPTY_EFFECT = 4294967295  # 0xFFFFFFFF

# In-game per-character relic storage cap. Source: community-verified in-game
# limit (the shop refuses purchases that would exceed it). Mirrored in the
# frontend as RELIC_CAP (frontend/src/components/inventory/types.ts).
RELIC_STORAGE_CAP = 1950

# Character names (index 0-9 = playable; "All" used as a UI filter only)
CHARACTER_NAME_ID = [100000, 100030, 100050, 100010, 100040, 100090,
                     100070, 100060, 110000, 110010]
CHARACTER_NAMES = [
    'Wylder', 'Guardian', 'Ironeye', 'Duchess', 'Raider',
    'Revenant', 'Recluse', 'Executor', 'Scholar', 'Undertaker', 'All',
]

# Per-Nightfarer applicability flags on AttachEffectParam. Source: datamined
# AttachEffectParam.csv columns allowWylder..allowUndertaker (one per playable
# character, in this exact order — positionally aligned with CHARACTER_NAMES
# minus the trailing "All" filter entry).
#
# A 0 means the game greys the effect out for that Nightfarer and it does
# nothing: either the effect is character-exclusive (e.g. "[Wylder] Character
# Skill inflicts Blood Loss") or its ash of war is incompatible with that
# character's starting armament (e.g. "Changes compatible armament's skill to
# Seppuku" on Raider, who starts with a colossal weapon; the sorcery variants
# require Recluse's staff and the incantation variants Revenant's seal).
# Confirmed in-game: a greyed effect is inert and does not conflict with or
# override the effects that do apply.
#
# The armament-skill flags follow directly from each Nightfarer's starting
# armament (user-confirmed, and consistent with every allow* flag checked):
#   Wylder greatsword + small shield | Guardian halberd + greatshield
#   Ironeye bow (only Rain of Arrows qualifies) | Duchess dagger
#   Raider colossal weapon | Revenant claws + sacred seal (incantations)
#   Recluse staff (sorceries) | Executor katana
#   Scholar thrusting sword (no staff → no sorceries) | Undertaker hammer
ALLOW_COLUMNS = [
    'allowWylder', 'allowGuardian', 'allowIroneye', 'allowDuchess',
    'allowRaider', 'allowRevenant', 'allowRecluse', 'allowExecutor',
    'allowScholar', 'allowUndertaker',
]

# Relic color index -> name (matches relicColor column in EquipParamAntique.csv)
COLOR_MAP = ["Red", "Blue", "Yellow", "Green", "White"]

# Relic color -> hex for display
RELIC_COLOR_HEX = {
    'Red':    '#FF4444',
    'Blue':   '#4488FF',
    'Yellow': '#B8860B',
    'Green':  '#44BB44',
    'White':  '#AAAAAA',
    None:     '#888888',
}

LANGUAGE_MAP: dict[str, str] = {
    "ar_AE": "العربية (الإمارات)",
    "de_DE": "Deutsch",
    "en_US": "English",
    "es_AR": "Español (Argentina)",
    "es_ES": "Español (España)",
    "fr_FR": "Français",
    "it_IT": "Italiano",
    "ja_JP": "日本語",
    "ko_KR": "한국어",
    "pl_PL": "Polski",
    "pt_BR": "Português (Brasil)",
    "ru_RU": "Русский",
    "th_TH": "ไทย",
    "zh_CN": "简体中文",
    "zh_TW": "繁體中文 (台灣)",
}

# Relic ID ranges by category
RELIC_GROUPS: dict[str, tuple[int, int]] = {
    "store_102":  (100,      199),
    "store_103":  (200,      299),
    "unique_1":   (1000,     2100),
    "unique_2":   (10000,    19999),
    "illegal":    (20000,    30035),
    "reward_0":   (1000000,  1000999),
    "reward_1":   (1001000,  1001999),
    "reward_2":   (1002000,  1002999),
    "reward_3":   (1003000,  1003999),
    "reward_4":   (1004000,  1004999),
    "reward_5":   (1005000,  1005999),
    "reward_6":   (1006000,  1006999),
    "reward_7":   (1007000,  1007999),
    "reward_8":   (1008000,  1008999),
    "reward_9":   (1009000,  1009999),
    "deep_102":   (2000000,  2009999),
    "deep_103":   (2010000,  2019999),
}


def is_unique_relic(real_id: int) -> bool:
    """Return True if real_id belongs to a unique (non-duplicatable) relic category."""
    lo1, hi1 = RELIC_GROUPS["unique_1"]
    lo2, hi2 = RELIC_GROUPS["unique_2"]
    return (lo1 <= real_id <= hi1) or (lo2 <= real_id <= hi2)
