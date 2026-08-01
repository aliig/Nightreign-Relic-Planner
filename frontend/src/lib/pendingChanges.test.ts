import { beforeEach, describe, expect, it, vi } from "vitest"

// The store is a module singleton (state + currentBaseBySlot + localStorage).
// Re-import a fresh copy per test so nothing leaks between cases.
type PendingModule = typeof import("./pendingChanges")

async function freshStore(): Promise<PendingModule> {
  localStorage.clear()
  vi.resetModules()
  return import("./pendingChanges")
}

describe("pendingChanges staleness guard", () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it("stamps the loaded profile id on the first edit", async () => {
    const pc = await freshStore()
    pc.noteSlotBase(0, "profA")
    pc.toggleSell(0, 123)
    expect(pc.readSlot(0).baseId).toBe("profA")
  })

  it("keeps the diff when the profile id is unchanged", async () => {
    const pc = await freshStore()
    pc.noteSlotBase(0, "profA")
    pc.toggleSell(0, 123)
    const cleared = pc.noteSlotBase(0, "profA")
    expect(cleared).toBe(false)
    expect(pc.readSlot(0).sells).toContain(123)
  })

  it("clears the diff when the profile id changed (re-upload)", async () => {
    const pc = await freshStore()
    pc.noteSlotBase(0, "profA")
    pc.toggleSell(0, 123)
    const cleared = pc.noteSlotBase(0, "profB")
    expect(cleared).toBe(true)
    expect(pc.readAll()[0]).toBeUndefined()
  })

  it("clears index-based loadout ops on a re-upload", async () => {
    const pc = await freshStore()
    pc.noteSlotBase(0, "profA")
    pc.addLoadoutOp(0, { kind: "delete", index: 2, name: "X" })
    expect(pc.noteSlotBase(0, "profB")).toBe(true)
    expect(pc.readAll()[0]).toBeUndefined()
  })

  it("adopts the current id for an unstamped (legacy) diff without clearing", async () => {
    const pc = await freshStore()
    // Edit with no prior noteSlotBase — leaves baseId unset.
    pc.toggleSell(0, 123)
    expect(pc.readSlot(0).baseId).toBeUndefined()
    const cleared = pc.noteSlotBase(0, "profA")
    expect(cleared).toBe(false)
    expect(pc.readSlot(0).sells).toContain(123)
    expect(pc.readSlot(0).baseId).toBe("profA")
  })

  it("reconciles each slot independently", async () => {
    const pc = await freshStore()
    pc.noteSlotBase(0, "profA")
    pc.noteSlotBase(1, "profB")
    pc.toggleSell(0, 1)
    pc.toggleSell(1, 2)
    // Slot 0's save was replaced; slot 1's was not.
    expect(pc.noteSlotBase(0, "profA2")).toBe(true)
    expect(pc.noteSlotBase(1, "profB")).toBe(false)
    expect(pc.readAll()[0]).toBeUndefined()
    expect(pc.readSlot(1).sells).toContain(2)
  })

  it("re-stamps from the current base after the prior diff was cleared", async () => {
    const pc = await freshStore()
    pc.noteSlotBase(0, "profA")
    pc.toggleSell(0, 1)
    pc.noteSlotBase(0, "profB") // stale -> cleared, currentBase now profB
    pc.toggleSell(0, 2) // fresh edit against the new save
    expect(pc.readSlot(0).baseId).toBe("profB")
    expect(pc.noteSlotBase(0, "profB")).toBe(false)
  })
})

describe("pendingChanges mints (Relic Rites)", () => {
  beforeEach(() => {
    localStorage.clear()
  })

  const spec = (realId: number) => ({
    real_id: realId,
    item_id: realId + 0x80000000,
    effects: [1, 2, 3],
    curses: [4294967295, 4294967295, 4294967295],
    name: `Relic ${realId}`,
    color: "Red",
    tier: "Grand",
    isDeep: false,
    oddsSource: "exact",
  })

  it("stages mints with stable ids and accumulates the murk delta", async () => {
    const pc = await freshStore()
    pc.addMints(0, [spec(200), spec(201)], -1200)
    const s = pc.readSlot(0)
    expect(s.mints).toHaveLength(2)
    expect(s.mints[0].id).toBeTruthy()
    expect(s.mints[0].real_id).toBe(200)
    expect(s.murkDelta).toBe(-1200)
    pc.addMints(0, [spec(202)], -600)
    expect(pc.readSlot(0).mints).toHaveLength(3)
    expect(pc.readSlot(0).murkDelta).toBe(-1800)
  })

  it("drops the slot once the last mint is removed", async () => {
    const pc = await freshStore()
    pc.addMints(0, [spec(200), spec(201)], -1200)
    const firstId = pc.readSlot(0).mints[0].id
    pc.removeMint(0, firstId)
    expect(pc.readSlot(0).mints).toHaveLength(1)
    pc.removeMint(0, pc.readSlot(0).mints[0].id)
    expect(pc.readAll()[0]).toBeUndefined()
  })

  it("credits the removed keeper's sell value into the murk delta", async () => {
    const pc = await freshStore()
    pc.addMints(0, [spec(200), spec(201)], -1200)
    const firstId = pc.readSlot(0).mints[0].id
    pc.removeMint(0, firstId)
    // spec() is a 3-effect normal relic -> sold back for 550 (buy-then-sell).
    expect(pc.readSlot(0).murkDelta).toBe(-1200 + 550)
    // Removing the LAST mint cancels the session outright (slot dropped, no
    // lingering delta) rather than crediting another sale.
    pc.removeMint(0, pc.readSlot(0).mints[0].id)
    expect(pc.readAll()[0]).toBeUndefined()
  })

  it("is a no-op for an empty batch", async () => {
    const pc = await freshStore()
    pc.addMints(0, [], 0)
    expect(pc.readAll()[0]).toBeUndefined()
  })

  it("clears staged mints on a re-upload (stale save)", async () => {
    const pc = await freshStore()
    pc.noteSlotBase(0, "profA")
    pc.addMints(0, [spec(200)], -600)
    expect(pc.noteSlotBase(0, "profB")).toBe(true)
    expect(pc.readAll()[0]).toBeUndefined()
  })
})
