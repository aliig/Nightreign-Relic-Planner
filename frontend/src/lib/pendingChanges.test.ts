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
