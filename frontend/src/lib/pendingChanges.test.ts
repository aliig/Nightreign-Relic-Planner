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

  it("assigns unique negative synthetic handles across slots", async () => {
    const pc = await freshStore()
    pc.addMints(0, [spec(200), spec(201)], -1200)
    pc.addMints(1, [spec(202)], -600)
    const all = [
      ...pc.readSlot(0).mints.map((m) => m.handle),
      ...pc.readSlot(1).mints.map((m) => m.handle),
    ]
    expect(all.every((h) => h < 0)).toBe(true)
    expect(new Set(all).size).toBe(all.length)
  })

  it("keeps handles stable across a reload", async () => {
    const pc = await freshStore()
    pc.addMints(0, [spec(200), spec(201)], -1200)
    const before = pc.readSlot(0).mints.map((m) => m.handle)
    vi.resetModules()
    const reloaded: PendingModule = await import("./pendingChanges")
    expect(reloaded.readSlot(0).mints.map((m) => m.handle)).toEqual(before)
  })

  it("backfills handles for pre-feature staged mints on load", async () => {
    localStorage.clear()
    // A diff persisted before MintSpec.handle existed.
    localStorage.setItem(
      "pendingChanges",
      JSON.stringify({
        0: {
          sells: [],
          favorites: {},
          loadoutOps: [],
          mints: [
            { ...spec(200), id: "op_legacy_1" },
            { ...spec(201), id: "op_legacy_2" },
          ],
          murkDelta: -1200,
          meta: {},
        },
      }),
    )
    vi.resetModules()
    const pc: PendingModule = await import("./pendingChanges")
    const handles = pc.readSlot(0).mints.map((m) => m.handle)
    expect(handles.every((h) => h < 0)).toBe(true)
    expect(new Set(handles).size).toBe(2)
  })
})

describe("pendingChanges staged-diff views", () => {
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

  it("stagedKey is empty when clean and stable under staging order", async () => {
    const pc = await freshStore()
    expect(pc.stagedKey(pc.readSlot(0))).toBe("")
    pc.toggleSell(0, 5)
    pc.toggleSell(0, 3)
    const a = pc.stagedKey(pc.readSlot(0))
    // Same sells staged in the opposite order -> same key.
    pc.clearAll()
    pc.toggleSell(0, 3)
    pc.toggleSell(0, 5)
    expect(pc.stagedKey(pc.readSlot(0))).toBe(a)
    // Favorites/loadout ops don't touch the inventory -> key stays put.
    pc.setFavorite(0, 3, true)
    expect(pc.stagedKey(pc.readSlot(0))).toBe(a)
    // A mint changes it.
    pc.addMints(0, [spec(200)], -600)
    expect(pc.stagedKey(pc.readSlot(0))).not.toBe(a)
  })

  it("stagedFields maps to the backend wire shape", async () => {
    const pc = await freshStore()
    pc.toggleSell(0, 42)
    pc.addMints(0, [spec(200)], -600)
    const s = pc.readSlot(0)
    const fields = pc.stagedFields(s)
    expect(fields.staged_sells).toEqual([42])
    expect(fields.staged_mints).toHaveLength(1)
    expect(fields.staged_mints[0]).toEqual({
      handle: s.mints[0].handle,
      real_id: 200,
      effects: [1, 2, 3],
      curses: [4294967295, 4294967295, 4294967295],
    })
  })

  it("removeMint cascades staged loadout ops referencing the mint", async () => {
    const pc = await freshStore()
    pc.addMints(0, [spec(200), spec(201)], -1200)
    const [minted, other] = pc.readSlot(0).mints
    pc.addLoadoutOp(0, {
      kind: "add",
      character: "Wylder",
      vessel_id: 1,
      ga_handles: [minted.handle, 0xc0000001],
      name: "Uses Mint",
    })
    pc.addLoadoutOp(0, {
      kind: "add",
      character: "Wylder",
      vessel_id: 2,
      ga_handles: [0xc0000002],
      name: "No Mint",
    })

    const refs = pc.mintReferences(pc.readSlot(0), minted.handle)
    expect(refs).toHaveLength(1)
    expect(refs[0].kind === "add" && refs[0].name).toBe("Uses Mint")
    expect(pc.mintReferences(pc.readSlot(0), other.handle)).toHaveLength(0)

    pc.removeMint(0, minted.id)
    const after = pc.readSlot(0)
    expect(after.mints).toHaveLength(1)
    expect(after.loadoutOps).toHaveLength(1)
    expect(after.loadoutOps[0].kind === "add" && after.loadoutOps[0].name).toBe(
      "No Mint",
    )
  })

  it("summarizePending rolls up per slot", async () => {
    const pc = await freshStore()
    pc.toggleSell(0, 1, { name: "R1", murk: 150 })
    pc.toggleSell(0, 2, { name: "R2", murk: 550 })
    pc.addMints(0, [spec(200)], -600)
    pc.setFavorite(1, 9, true)
    const rows = pc.summarizePending(pc.readAll())
    expect(rows).toHaveLength(2)
    expect(rows[0]).toMatchObject({
      slot: 0,
      sells: 2,
      sellRefund: 700,
      mints: 1,
      murkDelta: -600,
    })
    expect(rows[1]).toMatchObject({ slot: 1, favorites: 1 })
  })
})
