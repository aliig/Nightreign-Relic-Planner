# Relic-Purchase (Flatstone) RNG — Reverse-Engineering Record

**Status: EXACT / SOLVED.** The color **and** tier distribution of every Small Jar
Bazaar flatstone purchase is read directly from `regulation.bin`. No estimate,
no event-script (EMEVD) dependency. This doc lets a future maintainer reproduce
the extraction after a game patch and re-confirm the odds.

- **Extractor:** [`tools/populate_resources.py`](./populate_resources.py) →
  `generate_relic_lots_json()` (+ `_resolve_item_table()`).
- **Output consumed by the app:** `nrplanner/resources/json/relic_lots.json`
  (loaded by `backend/app/core/game_data.py::get_relic_lots`, used by
  `nrplanner/generator.py::RelicGenerator._pick_template`).
- **Investigated:** 2026-07, against `regulation.bin` dated 2025-03-31
  (contains both the v1.02 `100–135` and v1.03 `200–235` template sets).

---

## 1. The full acquisition chain (all inside `regulation.bin`)

A flatstone is bought from a shop, which rolls an **Item Table**, which names the
relic **template**. Three params, in order:

```
ShopLineupParam row  (equipType == 5  "Item Table";  equipId = table id)
   └─▶ ItemTableParam  rows whose ROW-ID == that equipId   (each row = 1 weighted entry)
          ├─ itemCategory 5 (Relic) ─▶ EquipParamAntique template  (a leaf)
          └─ itemCategory 7 (Item Table) ─▶ nested ItemTableParam table  (recurse)
   └─▶ EquipParamAntique  template = the rolled relic (color, tier, effect pools)
```

The key insight that unblocked this: **`equipType == 5` literally means "Item
Table" and resolves to `ItemTableParam`, not `ItemLotParam`.** That is why the
lot ids `49020 / 49300 / …` were never found as row-ids in `ItemLotParam_map` /
`ItemLotParam_enemy` — they are `ItemTableParam` row-ids. `ItemTableParam` has
34 730 rows and row-ids **repeat** (it is a flat "table" param like
`AttachEffectTableParam`: the row-id is the table id, each row is one entry).

### 1a. `ShopLineupParam` (row size 64) — the shop entries

Field offsets (little-endian), from `_parse_shop_lineup_param`:

| field | offset | type | meaning |
|---|---|---|---|
| `equipId` | `0x04` | i32 | **the `ItemTableParam` table id to roll** |
| `value` | `0x08` | i32 | price |
| `equipType` | `0x1B` | u8 | `5` = Item Table (`_SHOP_EQUIPTYPE_ITEM_TABLE`) |
| `costType` | `0x1C` | u8 | currency: `0`=Runes, `4`=Murk, `5`=Sovereign Sigil |

`SHOP_LINEUP_EQUIPTYPE` enum (Smithbox NR paramdef): `0`=Weapon `1`=Protector
`2`=Accessory `3`=Good `4`=Relic **`5`=Item Table** `6`=CustomWeapon.

**The 12 flatstone shop rows (the only `equipType==5` rows in the param):**

| shop row-id(s) | equipId (table) | value | costType | flatstone |
|---|---|---|---|---|
| 10110, 10112 | 49000 | 600 | 4 Murk | Scenic (v1.02) |
| 10114 | 49020 | 600 | 4 Murk | Scenic (v1.03) |
| 10111, 10113 | 49100 | 1800 | 4 Murk | Deep Scenic (v1.02) |
| 10115 | 49300 | 1800 | 4 Murk | Deep Scenic (v1.03) |
| 13100, 13101 | 49010 | 5 | 5 Sigil | Large Scenic (v1.02) |
| 13102 | 49030 | 5 | 5 Sigil | Large Scenic (v1.03) |
| 13110, 13111 | 49200 | 10 | 5 Sigil | Deep Large (v1.02) |
| 13112 | 49400 | 10 | 5 Sigil | Deep Large (v1.03) |

(The multiple rows per table are the same flatstone stocked by different NPCs /
unlock flags; all share the price. Prices match the community wiki exactly.)

### 1b. `ItemTableParam` (row size 36) — the roll table

Field offsets used (little-endian), from `_resolve_item_table`:

| field | offset | type | meaning |
|---|---|---|---|
| `itemCategory` | `0x04` | i32 | `5` = relic template leaf; `7` = nested Item Table |
| `itemId` | `0x08` | i32 | relic template id (cat 5) **or** nested table id (cat 7) |
| `chanceWeight` | `0x0C` | **i16** | weight (**low 16 bits**; `0x0E` is a separate field, always `1`) |

`ITEMLOT_ITEMCATEGORY` enum: `0`=None `1`=Good `2`=Weapon `3`=Armor
`4`=Accessory **`5`=Relic** `6`=CustomWeapon **`7`=Item Table (nested)**.

> The `chanceWeight` is a 16-bit field: the raw i32 at `0x0C` reads e.g. `65581`
> = `45 | (1<<16)`. Take `& 0xFFFF` → `45`. The community slavone calculator uses
> the identical `& 0xFFFF` decode on `AttachEffectTableParam` weights, corroborating
> the field width.

### 1c. `EquipParamAntique` (row size 48) — the relic template

Field offsets used, from `_parse_equip_param_antique`:

| field | offset | type | meaning |
|---|---|---|---|
| `relicColor` | `0x04` | u8 | 0=Red 1=Blue 2=Yellow 3=Green 4=White |
| `isDeepRelic` | `0x07` | bit0 | deep relic flag |
| `attachEffectTableId_1/2/3` | `0x10/0x14/0x18` | i32 | primary effect pools; **tier = count of non-`-1`** (1=Delicate, 2=Polished, 3=Grand) |
| `attachEffectTableId_curse1/2/3` | `0x20/0x24/0x28` | i32 | curse pools (deep only) |

**Template id layout** (all four colors present, **no White** — White is not
referenced by any flatstone table):

- Scenic v1.02 `100–135`, v1.03 `200–235`: 4 colors × 3 tiers × 3 copies = 36.
  Within a color the 3 consecutive ids are `T1,T2,T3` (e.g. 100=Red Delicate,
  101=Red Polished, 102=Red Grand).
- Deep v1.02 `2000000–2003322`, v1.03 `2010000–2013322`: 4 colors × (6 T1, 9 T2,
  12 T3) = 108, further grouped by **curse count** (see §2b).

---

## 2. The EXACT distribution, per flatstone variant

**Color is uniform 25% (Red/Blue/Yellow/Green) in every pool** — proven, not
assumed: summing `chanceWeight` per color gives an identical total for each of the
four colors (each color's templates carry identical weights), and no table
references a White template. Tier is as follows.

### 2a. Scenic — flat table, tier weights **45 / 35 / 20**

`ItemTableParam` table `49000` (v1.02) / `49020` (v1.03) — 36 direct `cat=5`
rows, `chanceWeight` by tier:

| tier | chanceWeight (per template) | aggregate |
|---|---|---|
| Delicate (T1) | **45** | 45% |
| Polished (T2) | **35** | 35% |
| Grand (T3) | **20** | 20% |

Evidence (table `49000`): row `itemId=100 (Red Delicate) weight=45`,
`101 (Red Polished) weight=35`, `102 (Red Grand) weight=20`, repeating for all
12 color-slots. Per-color total = 3·45 + 3·35 + 3·20 = **300** (→ 300/1200 = 25%).
**Uniform within a tier** (every copy of a (color,tier) has the same weight).

### 2b. Deep — nested table, tier weights **10 / 50 / 40**

`ItemTableParam` table `49100` (v1.02) / `49300` (v1.03) is `cat=7` nested. Top
level selects the tier; each tier sub-table then selects by **curse count**. Full
tree (v1.02; v1.03 sub-ids are `+200`, e.g. `49110→49310`):

```
49100  (Deep Scenic, v1.02)
 ├ 49110  Delicate  w=100 ─┬ 49120  T1, 0 curses  w=100   (12 templates)
 │            (→ 10%)       └ 49121  T1, 1 curse   w=100   (12 templates)
 ├ 49111  Polished  w=500 ─┬ 49130  T2, 0 curses  w=300   (12)
 │            (→ 50%)       ├ 49131  T2, 1 curse   w=500   (12)
 │                          └ 49132  T2, 2 curses  w=200   (12)
 └ 49112  Grand     w=400 ─┬ 49140  T3, 0 curses  w=400   (12)
              (→ 40%)       ├ 49141  T3, 1 curse   w=300   (12)
                            ├ 49142  T3, 2 curses  w=200   (12)
                            └ 49143  T3, 3 curses  w=100   (12)
```

Top-level weights `100 / 500 / 400` (sum 1000) ⇒ **Delicate 10% / Polished 50% /
Grand 40%**. Each curse-count leaf sub-table holds 12 templates = 4 colors × 3
copies, uniform → **color stays 25%**, but **per-template weight is NON-uniform
within a tier** (the curse-count split). The generator entries encode this exactly
(deep pool distinct entry weights `{4,5,8,10,12,15,16,25}` after GCD reduction).

### 2c. Large & Deep-Large — Grand-only (100%)

- Large Scenic `49010`/`49030`: 12 direct `cat=5` rows, only the T3/Grand
  templates (`102,105,…,135`), equal weight → Grand 100%, uniform 25% color.
- Deep Large `49200`/`49400`: `cat=7` → single tier sub-table `49212`/`49412`
  (Grand), split by curse count `49240–49243` (w 400/300/200/100). Grand 100%,
  color 25%, curse-count-weighted within.

### Confidence

**Very high / definitive.** The weights are literal bytes in `regulation.bin`,
the chain is fully closed (shop → table → template with matching ids and correct
prices), the color-uniformity is arithmetically proven, and a Monte-Carlo through
the real `RelicGenerator` reproduces 45/35/20 and 10/50/40 with 25% color and
`odds_source == "exact"`. Community effect calculators (slavone, ip1259)
independently confirm the template ranges and effect-roll mechanics; neither
publishes the tier meta-roll, so this is the primary source for it.

**→ GO.** The random-tier purchase is 1:1 with the game.

---

## 3. Re-extraction after a game patch

Everything comes from `regulation.bin` (+ the game's `oo2core_9_win64.dll` for
Oodle and the AES key already in `populate_resources.py::NR_REGULATION_KEY`). No
archive/BDT or event files are needed for the odds.

1. **Run the generator** (writes only `relic_lots.json`, not the big CSVs — the
   full script also refreshes CSV/FMG resources, which is fine but noisier):
   ```
   uv run tools/populate_resources.py --game-dir "<...>/ELDEN RING NIGHTREIGN/Game"
   ```
   Or regenerate just the lots in-memory (pattern used during RE — decrypt
   `regulation.bin`, `parse_param_rows` for `EquipParamAntique` / `ShopLineupParam`
   / `ItemTableParam`, call `generate_relic_lots_json(...)`).

2. **Spot-check the shop rows still map as in §1a:** every `ShopLineupParam` row
   with `equipType==5` should have `equipId ∈ {49000,49010,49020,49030,49100,
   49200,49300,49400}` and the prices in the table. A new patch that renumbers
   these breaks the mapping — update `RELIC_LOT_SHOP_EQUIPID`.

3. **Spot-check the tier weights:** in `ItemTableParam` table `49000`, the three
   `chanceWeight`s per color must be `45/35/20`; table `49100` top level must be
   `100/500/400`. If FromSoft rebalanced, `relic_lots.json` updates automatically
   (weights are read, not hardcoded) — just re-confirm `tier_weights` in the
   output reads `{1:45,2:35,3:20}` (scenic) and `{1:10,2:50,3:40}` (deep).

4. **Confirm odds** (fast, no effect roll):
   ```python
   gen = RelicGenerator(ds, lots=json.load(open("nrplanner/resources/json/relic_lots.json")))
   # sample gen._pick_template(is_deep, version, "random", None, None, rng) ~1e5 times;
   # expect tier 45/35/20 (scenic) or 10/50/40 (deep), color 25%, odds_source "exact".
   ```

5. If a **new patch adds a v1.04 template set / new flatstone**, add its key to
   `RELIC_LOT_POOLS` + `RELIC_LOT_SHOP_EQUIPID` (find the new `equipType==5` shop
   row and its `equipId`), and the resolver handles the rest.

---

## 4. What remains uncertain / unrecoverable

**Nothing material to the tier/color question** — it is fully in `regulation.bin`.

Minor / out of scope:

- **Semantic meaning of the deep curse-count weighting** is inferred (sub-tables
  cleanly partition by number of curse slots: 0/1/2/3), but the *labels* are our
  interpretation; the *weights* are exact regardless.
- **Generator plumbing for Large / Deep-Large pools:** `relic_lots.json` carries
  correct `large_scenic_* / deep_large_*` entries, but
  `RelicGenerator._lot_key()` maps `(is_deep, version)` to only `scenic_* /
  deep_*`, so the generator cannot currently *select* the Large pools by key. This
  is a generator feature gap, **not** a data problem (the Large flatstone tier is
  trivially Grand-100% anyway). If Large-flatstone simulation is wanted, extend
  `_lot_key`/`roll()` with a "large" flag.
- **The built-in fallback** `generator.py::_APPROX_TIER_WEIGHTS = {1:55,2:33,3:12}`
  is now stale (real scenic is 45/35/20; deep 10/50/40 can't be one constant). It
  is only used when `relic_lots.json` is absent, which never happens in a normal
  build — left untouched to keep this change scoped to the extraction, but worth
  updating to `{1:45,2:35,3:20}` if the fallback path is ever exercised.
