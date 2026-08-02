import { describe, expect, it } from "vitest"

import {
  findSavedLoadoutMatch,
  relicContentKey,
  type SavedLoadoutRef,
} from "./savedLoadoutMatch"

const EMPTY = 4294967295

/** A relic copy: distinct ga_handle, content from (realId, e1). */
const relic = (handle: number, realId: number, e1 = 100) => ({
  ga_handle: handle,
  real_id: realId,
  effects: [e1, EMPTY, EMPTY],
  curses: [EMPTY, EMPTY, EMPTY],
})

const loadout = (
  vesselId: number,
  handles: number[],
  name = "L",
  index = 0,
): SavedLoadoutRef => ({
  index,
  name,
  vessel_id: vesselId,
  ga_handles: handles,
})

/** ga_handle → content key map over the given relics. */
const mapOf = (...relics: ReturnType<typeof relic>[]) =>
  new Map(relics.map((r) => [r.ga_handle, relicContentKey(r)]))

describe("findSavedLoadoutMatch", () => {
  // Inventory used across cases: 10/20 are DISTINCT contents; 30/31 are two
  // physical copies of the SAME content; 40 is a third distinct content.
  const rA = relic(10, 1000, 1)
  const rB = relic(20, 2000, 2)
  const rDup1 = relic(30, 3000, 3)
  const rDup2 = relic(31, 3000, 3)
  const rC = relic(40, 4000, 4)
  const handleMap = mapOf(rA, rB, rDup1, rDup2, rC)

  it("matches identical handles regardless of slot order (exact)", () => {
    const m = findSavedLoadoutMatch(
      7,
      [rA, rB],
      [loadout(7, [20, 10])],
      handleMap,
    )
    expect(m).toBeDefined()
    expect(m?.equivalent).toBe(false)
  })

  it("exact tier works without a content map", () => {
    const m = findSavedLoadoutMatch(7, [rA, rB], [loadout(7, [10, 20])])
    expect(m?.equivalent).toBe(false)
  })

  it("matches a different physical copy of the same content (equivalent)", () => {
    // Result placed copy 31; the saved loadout holds copy 30.
    const m = findSavedLoadoutMatch(
      7,
      [rA, rDup2],
      [loadout(7, [10, 30], "Offense")],
      handleMap,
    )
    expect(m).toBeDefined()
    expect(m?.equivalent).toBe(true)
    expect(m?.loadout.name).toBe("Offense")
  })

  it("prefers an exact match over an equivalent one", () => {
    const m = findSavedLoadoutMatch(
      7,
      [rA, rDup1],
      [loadout(7, [10, 31], "equiv-copy", 0), loadout(7, [10, 30], "exact", 1)],
      handleMap,
    )
    expect(m?.loadout.name).toBe("exact")
    expect(m?.equivalent).toBe(false)
  })

  it("never matches across vessels", () => {
    expect(
      findSavedLoadoutMatch(7, [rA, rB], [loadout(8, [10, 20])], handleMap),
    ).toBeUndefined()
  })

  it("rejects different contents", () => {
    expect(
      findSavedLoadoutMatch(7, [rA, rC], [loadout(7, [10, 20])], handleMap),
    ).toBeUndefined()
  })

  it("compares as a multiset — duplicate counts must agree", () => {
    // Loadout has both copies of the dup content; result has one dup + rA.
    expect(
      findSavedLoadoutMatch(7, [rA, rDup1], [loadout(7, [30, 31])], handleMap),
    ).toBeUndefined()
    // Both sides hold the two copies (under different handles per side is
    // impossible here, but swapped order is): equivalent... exact, actually,
    // since the handle SET {30,31} is identical.
    const m = findSavedLoadoutMatch(
      7,
      [rDup1, rDup2],
      [loadout(7, [31, 30])],
      handleMap,
    )
    expect(m?.equivalent).toBe(false)
  })

  it("skips loadouts with handles missing from the map (unverifiable)", () => {
    // Handle 99 (sold / unknown) can't be resolved → no content match, even
    // though the lengths agree.
    expect(
      findSavedLoadoutMatch(7, [rA, rDup2], [loadout(7, [10, 99])], handleMap),
    ).toBeUndefined()
  })

  it("ignores empty (0) slots in saved loadouts", () => {
    const m = findSavedLoadoutMatch(
      7,
      [rA, rDup2],
      [loadout(7, [10, 30, 0])],
      handleMap,
    )
    expect(m?.equivalent).toBe(true)
  })

  it("requires equal relic counts", () => {
    expect(
      findSavedLoadoutMatch(7, [rA], [loadout(7, [10, 30])], handleMap),
    ).toBeUndefined()
  })
})
