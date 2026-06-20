/**
 * Pure relic-filtering logic for the inventory. No React — this module is the
 * testable core behind the InventoryFilters toolbar.
 *
 * Two layers, by design:
 *  - `applyFilters` matches **intrinsic** relic fields (name/color/tier/deep/
 *    effects) and is the cheap first pass.
 *  - `matchesState` matches **live/derived** axes (sellable/equipped/in-a-build/
 *    bookmarked) from a small `RelicDerived` the caller computes — those depend on
 *    usage data and pending favorite edits that only live in RelicManager.
 */
import type { ManagedRelic } from "./types"

/** Any/Yes/No axis. "all" = no constraint. */
export type TriState = "all" | "yes" | "no"

export type FilterState = {
  search: string
  /** Multi-select; [] = any color. */
  colors: string[]
  /** Multi-select; [] = any tier. */
  tiers: string[]
  deep: "all" | "deep" | "standard"
  /** Derived: a relic is sellable when not equipped, bookmarked, or unique. */
  sellable: "all" | "sellable" | "locked"
  equipped: TriState
  /** "In a build" — used by at least one build. */
  used: TriState
  bookmarked: TriState
  effectFilter: number[]
  /** How selected effects combine: and (all) / or (any) / not (none). */
  effectMode: "and" | "or" | "not"
}

export const EMPTY_FILTER: FilterState = {
  search: "",
  colors: [],
  tiers: [],
  deep: "all",
  sellable: "all",
  equipped: "all",
  used: "all",
  bookmarked: "all",
  effectFilter: [],
  effectMode: "and",
}

/** Intrinsic (relic-shape) filtering: name, color, tier, deep, effects. */
export function applyFilters(
  relics: ManagedRelic[],
  f: FilterState,
  effectMap: Map<number, string>,
): ManagedRelic[] {
  return relics.filter((r) => {
    if (f.search && !r.name.toLowerCase().includes(f.search.toLowerCase()))
      return false
    if (f.colors.length > 0 && !f.colors.includes(r.color)) return false
    if (f.tiers.length > 0 && !f.tiers.includes(r.tier)) return false
    if (f.deep === "deep" && !r.isDeep) return false
    if (f.deep === "standard" && r.isDeep) return false

    if (f.effectFilter.length > 0) {
      // Match by display name so aliased effect IDs collapse together.
      const selectedNames = f.effectFilter
        .map((id) => effectMap.get(id))
        .filter(Boolean)
      const relicEffectNames = r.effects
        .map((id) => effectMap.get(id))
        .filter(Boolean)
      const has = (name: string | undefined) => relicEffectNames.includes(name)
      if (f.effectMode === "or") {
        if (!selectedNames.some(has)) return false
      } else if (f.effectMode === "not") {
        if (selectedNames.some(has)) return false
      } else if (!selectedNames.every(has)) {
        return false
      }
    }
    return true
  })
}

/** Live/derived per-relic state, computed by the caller for `matchesState`. */
export type RelicDerived = {
  equipped: boolean
  used: boolean
  favorite: boolean
  sellable: boolean
}

/** Derived-axis filtering: sellability, equipped, in-a-build, bookmarked (AND). */
export function matchesState(f: FilterState, d: RelicDerived): boolean {
  if (f.sellable === "sellable" && !d.sellable) return false
  if (f.sellable === "locked" && d.sellable) return false
  if (f.equipped === "yes" && !d.equipped) return false
  if (f.equipped === "no" && d.equipped) return false
  if (f.used === "yes" && !d.used) return false
  if (f.used === "no" && d.used) return false
  if (f.bookmarked === "yes" && !d.favorite) return false
  if (f.bookmarked === "no" && d.favorite) return false
  return true
}

/** How many State axes are constrained — drives the State facet's count badge. */
export function stateFacetCount(f: FilterState): number {
  return (
    (f.sellable !== "all" ? 1 : 0) +
    (f.equipped !== "all" ? 1 : 0) +
    (f.used !== "all" ? 1 : 0) +
    (f.bookmarked !== "all" ? 1 : 0)
  )
}

export function isFilterActive(f: FilterState): boolean {
  return (
    f.search !== "" ||
    f.colors.length > 0 ||
    f.tiers.length > 0 ||
    f.deep !== "all" ||
    f.sellable !== "all" ||
    f.equipped !== "all" ||
    f.used !== "all" ||
    f.bookmarked !== "all" ||
    f.effectFilter.length > 0
  )
}

/** A removable active-filter chip: applying `clear` drops just this constraint. */
export type FilterChip = {
  key: string
  label: string
  clear: Partial<FilterState>
}

const TRI_LABEL: Record<Exclude<TriState, "all">, string> = {
  yes: "Yes",
  no: "No",
}

/** Flatten the active filter into removable chips for the toolbar summary. */
export function activeFilterChips(
  f: FilterState,
  effectMap: Map<number, string>,
): FilterChip[] {
  const chips: FilterChip[] = []
  if (f.search)
    chips.push({ key: "search", label: `"${f.search}"`, clear: { search: "" } })
  for (const c of f.colors)
    chips.push({
      key: `color:${c}`,
      label: c,
      clear: { colors: f.colors.filter((x) => x !== c) },
    })
  for (const t of f.tiers)
    chips.push({
      key: `tier:${t}`,
      label: t,
      clear: { tiers: f.tiers.filter((x) => x !== t) },
    })
  if (f.deep !== "all")
    chips.push({
      key: "deep",
      label: f.deep === "deep" ? "Deep" : "Standard",
      clear: { deep: "all" },
    })
  if (f.sellable !== "all")
    chips.push({
      key: "sellable",
      label: f.sellable === "sellable" ? "Sellable" : "Locked",
      clear: { sellable: "all" },
    })
  if (f.equipped !== "all")
    chips.push({
      key: "equipped",
      label: `Equipped: ${TRI_LABEL[f.equipped]}`,
      clear: { equipped: "all" },
    })
  if (f.used !== "all")
    chips.push({
      key: "used",
      label: `In a build: ${TRI_LABEL[f.used]}`,
      clear: { used: "all" },
    })
  if (f.bookmarked !== "all")
    chips.push({
      key: "bookmarked",
      label: `Bookmarked: ${TRI_LABEL[f.bookmarked]}`,
      clear: { bookmarked: "all" },
    })
  for (const id of f.effectFilter) {
    const name = effectMap.get(id) ?? `#${id}`
    chips.push({
      key: `effect:${id}`,
      label: f.effectMode === "not" ? `Not ${name}` : name,
      clear: { effectFilter: f.effectFilter.filter((x) => x !== id) },
    })
  }
  return chips
}
