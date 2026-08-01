import { describe, expect, it } from "vitest"

import type { SlotPending } from "./pendingChanges"
import {
  type ContentTriple,
  classifyPending,
  slotCommitted,
} from "./uploadGate"

const EMPTY = 4294967295

const triple = (realId: number, e1 = 100): ContentTriple => ({
  real_id: realId,
  effects: [e1, EMPTY, EMPTY],
  curses: [EMPTY, EMPTY, EMPTY],
})

const fpOf = (t: ContentTriple) => [t.real_id, ...t.effects, ...t.curses]

function slot(partial: Partial<SlotPending>): SlotPending {
  return {
    sells: [],
    favorites: {},
    loadoutOps: [],
    mints: [],
    murkDelta: 0,
    meta: {},
    ...partial,
  }
}

const mintOf = (t: ContentTriple, handle: number) => ({
  id: `m${handle}`,
  handle,
  real_id: t.real_id,
  item_id: t.real_id + 2147483648,
  effects: t.effects,
  curses: t.curses,
  name: "Mint",
  color: "Red",
  tier: "Delicate",
  isDeep: false,
  oddsSource: "exact",
})

describe("uploadGate committed-detection", () => {
  const kept = triple(1, 200) // an untouched relic present in both saves
  const soldT = triple(2, 300)
  const mintT = triple(3, 400)

  it("detects a committed diff (sell gone, mint present)", () => {
    const s = slot({
      sells: [111],
      meta: { 111: { name: "Sold", fp: fpOf(soldT) } },
      mints: [mintOf(mintT, -1)],
    })
    const oldRelics = [kept, soldT]
    const newRelics = [kept, mintT]
    expect(slotCommitted(s, oldRelics, newRelics)).toBe(true)
  })

  it("stays committed when the user kept playing (gains/losses elsewhere)", () => {
    const s = slot({
      sells: [111],
      meta: { 111: { name: "Sold", fp: fpOf(soldT) } },
      mints: [mintOf(mintT, -1)],
    })
    // Played on: gained another copy of the minted content + a brand-new relic.
    const newRelics = [kept, mintT, mintT, triple(9, 900)]
    expect(slotCommitted(s, [kept, soldT], newRelics)).toBe(true)
  })

  it("flags divergence when the mint is absent from the new save", () => {
    const s = slot({ mints: [mintOf(mintT, -1)] })
    expect(slotCommitted(s, [kept], [kept])).toBe(false)
  })

  it("flags divergence when the sold relic is still present", () => {
    const s = slot({
      sells: [111],
      meta: { 111: { name: "Sold", fp: fpOf(soldT) } },
    })
    expect(slotCommitted(s, [kept, soldT], [kept, soldT])).toBe(false)
  })

  it("counts duplicate contents as a multiset", () => {
    // Own two identical relics, sell ONE. Committed save has exactly one left.
    const s = slot({
      sells: [111],
      meta: { 111: { name: "Sold", fp: fpOf(soldT) } },
    })
    expect(slotCommitted(s, [soldT, soldT], [soldT])).toBe(true)
    // Both still present -> the sell never happened.
    expect(slotCommitted(s, [soldT, soldT], [soldT, soldT])).toBe(false)
  })

  it("mint of a content identical to an owned relic requires a count increase", () => {
    const s = slot({ mints: [mintOf(mintT, -1)] })
    expect(slotCommitted(s, [mintT], [mintT])).toBe(false)
    expect(slotCommitted(s, [mintT], [mintT, mintT])).toBe(true)
  })

  it("gates on ambiguity: legacy sells without fingerprints", () => {
    const s = slot({ sells: [111], meta: { 111: { name: "Sold" } } })
    expect(slotCommitted(s, [kept, soldT], [kept])).toBe(false)
  })

  it("gates on ambiguity: nothing content-verifiable (loadout-only diff)", () => {
    const s = slot({
      loadoutOps: [{ id: "a", kind: "delete", index: 0, name: "X" }],
    })
    expect(slotCommitted(s, [kept], [kept])).toBe(false)
  })

  it("gates when the slot is missing from either save", () => {
    const s = slot({ mints: [mintOf(mintT, -1)] })
    expect(slotCommitted(s, undefined, [mintT])).toBe(false)
    expect(slotCommitted(s, [kept], undefined)).toBe(false)
  })

  it("classifyPending splits slots independently", () => {
    const committedSlot = slot({ mints: [mintOf(mintT, -1)] })
    const divergentSlot = slot({ mints: [mintOf(mintT, -2)] })
    const pending = { 0: committedSlot, 2: divergentSlot }
    const oldBySlot = new Map([
      [0, [kept]],
      [2, [kept]],
    ])
    const newBySlot = new Map([
      [0, [kept, mintT]], // mint arrived
      [2, [kept]], // mint missing
    ])
    expect(classifyPending(pending, oldBySlot, newBySlot)).toEqual({
      committed: [0],
      divergent: [2],
    })
  })
})
