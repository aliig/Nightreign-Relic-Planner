"""Game constants — no mutable state."""

# Item type flags (upper 4 bits of gaitem_handle)
ITEM_TYPE_EMPTY  = 0x00000000
ITEM_TYPE_WEAPON = 0x80000000
ITEM_TYPE_ARMOR  = 0x90000000
ITEM_TYPE_RELIC  = 0xC0000000

# Sentinel value for empty effect slots in save data
EMPTY_EFFECT = 4294967295  # 0xFFFFFFFF

# Character names (index 0-9 = playable; "All" used as a UI filter only)
CHARACTER_NAME_ID = [100000, 100030, 100050, 100010, 100040, 100090,
                     100070, 100060, 110000, 110010]
CHARACTER_NAMES = [
    'Wylder', 'Guardian', 'Ironeye', 'Duchess', 'Raider',
    'Revenant', 'Recluse', 'Executor', 'Scholar', 'Undertaker', 'All',
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
