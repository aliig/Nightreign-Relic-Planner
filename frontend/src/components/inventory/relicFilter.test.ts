import { describe, expect, it } from "vitest"

import {
  activeFilterChips,
  applyFilters,
  EMPTY_FILTER,
  type FilterState,
  isFilterActive,
  matchesState,
  type RelicDerived,
} from "./relicFilter"
import type { ManagedRelic } from "./types"

function relic(over: Partial<ManagedRelic>): ManagedRelic {
  return {
    key: "k",
    gaHandle: 1,
    realId: 5000,
    name: "Test Relic",
    color: "Red",
    tier: "Grand",
    isDeep: false,
    effects: [],
    curses: [],
    isFavorite: false,
    equipped: false,
    ...over,
  }
}

const EFFECT_MAP = new Map<number, string>([
  [100, "Attack Up"],
  [200, "Defense Up"],
  [300, "Stamina Up"],
])

const f = (over: Partial<FilterState>): FilterState => ({
  ...EMPTY_FILTER,
  ...over,
})

const d = (over: Partial<RelicDerived>): RelicDerived => ({
  equipped: false,
  used: false,
  favorite: false,
  sellable: true,
  ...over,
})

describe("applyFilters", () => {
  const relics = [
    relic({ name: "Red Grand", color: "Red", tier: "Grand", effects: [100] }),
    relic({
      name: "Blue Delicate",
      color: "Blue",
      tier: "Delicate",
      isDeep: true,
      effects: [100, 200],
    }),
    relic({
      name: "Green Polished",
      color: "Green",
      tier: "Polished",
      effects: [300],
    }),
  ]

  it("matches by name (case-insensitive)", () => {
    expect(
      applyFilters(relics, f({ search: "blue" }), EFFECT_MAP).map(
        (r) => r.name,
      ),
    ).toEqual(["Blue Delicate"])
  })

  it("filters by multi-select colors (OR within the facet)", () => {
    const out = applyFilters(
      relics,
      f({ colors: ["Red", "Green"] }),
      EFFECT_MAP,
    )
    expect(out.map((r) => r.color).sort()).toEqual(["Green", "Red"])
  })

  it("empty colors means no color constraint", () => {
    expect(applyFilters(relics, f({ colors: [] }), EFFECT_MAP)).toHaveLength(3)
  })

  it("filters by tier", () => {
    expect(
      applyFilters(relics, f({ tiers: ["Grand"] }), EFFECT_MAP).map(
        (r) => r.name,
      ),
    ).toEqual(["Red Grand"])
  })

  it("filters deep vs standard", () => {
    expect(
      applyFilters(relics, f({ deep: "deep" }), EFFECT_MAP).map((r) => r.name),
    ).toEqual(["Blue Delicate"])
    expect(
      applyFilters(relics, f({ deep: "standard" }), EFFECT_MAP),
    ).toHaveLength(2)
  })

  it("effect mode AND requires all selected effects", () => {
    expect(
      applyFilters(
        relics,
        f({ effectFilter: [100, 200], effectMode: "and" }),
        EFFECT_MAP,
      ).map((r) => r.name),
    ).toEqual(["Blue Delicate"])
  })

  it("effect mode OR requires any selected effect", () => {
    const out = applyFilters(
      relics,
      f({ effectFilter: [100, 300], effectMode: "or" }),
      EFFECT_MAP,
    )
    expect(out.map((r) => r.name).sort()).toEqual([
      "Blue Delicate",
      "Green Polished",
      "Red Grand",
    ])
  })

  it("effect mode NOT excludes any match", () => {
    const out = applyFilters(
      relics,
      f({ effectFilter: [100], effectMode: "not" }),
      EFFECT_MAP,
    )
    expect(out.map((r) => r.name)).toEqual(["Green Polished"])
  })
})

describe("matchesState", () => {
  it("sellable filter", () => {
    expect(
      matchesState(f({ sellable: "sellable" }), d({ sellable: true })),
    ).toBe(true)
    expect(
      matchesState(f({ sellable: "sellable" }), d({ sellable: false })),
    ).toBe(false)
    expect(
      matchesState(f({ sellable: "locked" }), d({ sellable: false })),
    ).toBe(true)
  })

  it("equipped tri-state", () => {
    expect(matchesState(f({ equipped: "yes" }), d({ equipped: true }))).toBe(
      true,
    )
    expect(matchesState(f({ equipped: "no" }), d({ equipped: true }))).toBe(
      false,
    )
  })

  it("in-a-build tri-state", () => {
    expect(matchesState(f({ used: "yes" }), d({ used: true }))).toBe(true)
    expect(matchesState(f({ used: "no" }), d({ used: true }))).toBe(false)
  })

  it("bookmarked tri-state reads favorite", () => {
    expect(matchesState(f({ bookmarked: "yes" }), d({ favorite: true }))).toBe(
      true,
    )
    expect(matchesState(f({ bookmarked: "yes" }), d({ favorite: false }))).toBe(
      false,
    )
    expect(matchesState(f({ bookmarked: "no" }), d({ favorite: false }))).toBe(
      true,
    )
  })

  it("ANDs across axes", () => {
    expect(
      matchesState(
        f({ equipped: "yes", used: "no" }),
        d({ equipped: true, used: false }),
      ),
    ).toBe(true)
    expect(
      matchesState(
        f({ equipped: "yes", used: "no" }),
        d({ equipped: true, used: true }),
      ),
    ).toBe(false)
  })

  it("all-Any matches everything", () => {
    expect(matchesState(EMPTY_FILTER, d({}))).toBe(true)
  })
})

describe("isFilterActive / activeFilterChips", () => {
  it("EMPTY_FILTER is inactive with no chips", () => {
    expect(isFilterActive(EMPTY_FILTER)).toBe(false)
    expect(activeFilterChips(EMPTY_FILTER, EFFECT_MAP)).toEqual([])
  })

  it("produces a chip per active constraint", () => {
    const filter = f({
      colors: ["Red", "Blue"],
      deep: "deep",
      bookmarked: "yes",
      effectFilter: [100],
    })
    const labels = activeFilterChips(filter, EFFECT_MAP).map((c) => c.label)
    expect(labels).toContain("Red")
    expect(labels).toContain("Blue")
    expect(labels).toContain("Deep")
    expect(labels).toContain("Bookmarked: Yes")
    expect(labels).toContain("Attack Up")
    expect(isFilterActive(filter)).toBe(true)
  })

  it("chip.clear removes just that one constraint", () => {
    const filter = f({ colors: ["Red", "Blue"] })
    const chip = activeFilterChips(filter, EFFECT_MAP).find(
      (c) => c.label === "Red",
    )
    expect(chip?.clear).toEqual({ colors: ["Blue"] })
  })

  it("NOT-mode effect chips are prefixed", () => {
    const filter = f({ effectFilter: [100], effectMode: "not" })
    expect(activeFilterChips(filter, EFFECT_MAP)[0].label).toBe("Not Attack Up")
  })
})
