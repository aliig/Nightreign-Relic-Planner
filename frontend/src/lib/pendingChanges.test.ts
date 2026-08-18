import { beforeEach, describe, expect, it, vi } from "vitest"

import { findSavedLoadoutMatch, relicContentKey } from "./savedLoadoutMatch"

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

  it("commits the batch with stable ids and its absolute murk delta", async () => {
    const pc = await freshStore()
    pc.replaceRitesBatch(0, [spec(200), spec(201)], -1200)
    const s = pc.readSlot(0)
    expect(s.mints).toHaveLength(2)
    expect(s.mints[0].id).toBeTruthy()
    expect(s.mints[0].real_id).toBe(200)
    expect(s.murkDelta).toBe(-1200)
  })

  it("a re-run REPLACES the batch (same stream re-viewed, never stacked)", async () => {
    const pc = await freshStore()
    pc.replaceRitesBatch(0, [spec(200), spec(201)], -1200)
    pc.replaceRitesBatch(0, [spec(202)], -600)
    expect(pc.readSlot(0).mints.map((m) => m.real_id)).toEqual([202])
    expect(pc.readSlot(0).murkDelta).toBe(-600)
  })

  it("preserves a surviving mint's id/handle across a batch refresh", async () => {
    const pc = await freshStore()
    pc.replaceRitesBatch(0, [spec(200), spec(201)], -1200)
    const kept = pc.readSlot(0).mints[0]
    pc.replaceRitesBatch(
      0,
      [{ ...spec(200), id: kept.id, handle: kept.handle }, spec(202)],
      -1800,
    )
    const after = pc.readSlot(0).mints
    expect(after).toHaveLength(2)
    expect(after[0]).toMatchObject({ id: kept.id, handle: kept.handle })
    expect(after[1].handle).toBeLessThan(0)
    expect(after[1].handle).not.toBe(kept.handle)
  })

  it("keeps the all-sold loss when every mint is removed (batch still stands)", async () => {
    const pc = await freshStore()
    pc.replaceRitesBatch(0, [spec(200), spec(201)], -1200)
    pc.removeMint(0, pc.readSlot(0).mints[0].id)
    pc.removeMint(0, pc.readSlot(0).mints[0].id)
    // Both 3-effect normal relics sold back for 550 each; the buy/sell spread
    // stays committed — removing keepers never un-buys the batch.
    expect(pc.readSlot(0).mints).toHaveLength(0)
    expect(pc.readSlot(0).murkDelta).toBe(-1200 + 550 + 550)
  })

  it("credits the removed keeper's sell value into the murk delta", async () => {
    const pc = await freshStore()
    pc.replaceRitesBatch(0, [spec(200), spec(201)], -1200)
    const firstId = pc.readSlot(0).mints[0].id
    pc.removeMint(0, firstId)
    // spec() is a 3-effect normal relic -> sold back for 550 (buy-then-sell).
    expect(pc.readSlot(0).murkDelta).toBe(-1200 + 550)
  })

  it("an empty batch with no delta stages nothing", async () => {
    const pc = await freshStore()
    pc.replaceRitesBatch(0, [], 0)
    expect(pc.readAll()[0]).toBeUndefined()
  })

  it("an all-dud batch stages a pure loss (no mints, negative delta)", async () => {
    const pc = await freshStore()
    pc.replaceRitesBatch(0, [], -3_000)
    expect(pc.readSlot(0).mints).toHaveLength(0)
    expect(pc.readSlot(0).murkDelta).toBe(-3_000)
    expect(pc.murkAdjustment(pc.readSlot(0))).toBe(-3_000)
    // clearRitesBatch is the only way back to a clean slate.
    pc.clearRitesBatch(0)
    expect(pc.readAll()[0]).toBeUndefined()
  })

  it("replaceRitesBatch prunes loadout ops referencing dropped mints", async () => {
    const pc = await freshStore()
    pc.replaceRitesBatch(0, [spec(200), spec(201)], -1200)
    const [a, b] = pc.readSlot(0).mints
    pc.addLoadoutOp(0, {
      kind: "add",
      character: "Wylder",
      vessel_id: 1,
      ga_handles: [a.handle],
      name: "Uses A",
    })
    pc.addLoadoutOp(0, {
      kind: "add",
      character: "Wylder",
      vessel_id: 2,
      ga_handles: [b.handle],
      name: "Uses B",
    })
    // Refresh keeps only mint B.
    const dropped = pc.replaceRitesBatch(
      0,
      [{ ...spec(201), id: b.id, handle: b.handle }],
      -600,
    )
    expect(dropped).toEqual(['Add loadout "Uses A"'])
    expect(
      pc.readSlot(0).loadoutOps.map((o) => o.kind === "add" && o.name),
    ).toEqual(["Uses B"])
  })

  it("clearRitesBatch cancels mints, delta, and batch-referencing ops", async () => {
    const pc = await freshStore()
    pc.replaceRitesBatch(0, [spec(200)], -600)
    const m = pc.readSlot(0).mints[0]
    pc.addLoadoutOp(0, {
      kind: "add",
      character: "Wylder",
      vessel_id: 1,
      ga_handles: [m.handle],
      name: "Uses Mint",
    })
    pc.addLoadoutOp(0, { kind: "delete", index: 2, name: "Unrelated" })
    const dropped = pc.clearRitesBatch(0)
    expect(dropped).toEqual(['Add loadout "Uses Mint"'])
    const s = pc.readSlot(0)
    expect(s.mints).toHaveLength(0)
    expect(s.murkDelta).toBe(0)
    expect(s.loadoutOps).toHaveLength(1)
  })

  it("clears staged mints on a re-upload (stale save)", async () => {
    const pc = await freshStore()
    pc.noteSlotBase(0, "profA")
    pc.replaceRitesBatch(0, [spec(200)], -600)
    expect(pc.noteSlotBase(0, "profB")).toBe(true)
    expect(pc.readAll()[0]).toBeUndefined()
  })

  it("assigns unique negative synthetic handles across slots", async () => {
    const pc = await freshStore()
    pc.replaceRitesBatch(0, [spec(200), spec(201)], -1200)
    pc.replaceRitesBatch(1, [spec(202)], -600)
    const all = [
      ...pc.readSlot(0).mints.map((m) => m.handle),
      ...pc.readSlot(1).mints.map((m) => m.handle),
    ]
    expect(all.every((h) => h < 0)).toBe(true)
    expect(new Set(all).size).toBe(all.length)
  })

  it("keeps handles stable across a reload", async () => {
    const pc = await freshStore()
    pc.replaceRitesBatch(0, [spec(200), spec(201)], -1200)
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
    pc.replaceRitesBatch(0, [spec(200)], -600)
    expect(pc.stagedKey(pc.readSlot(0))).not.toBe(a)
  })

  it("stagedFields maps to the backend wire shape", async () => {
    const pc = await freshStore()
    pc.toggleSell(0, 42)
    pc.replaceRitesBatch(0, [spec(200)], -600)
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
    pc.replaceRitesBatch(0, [spec(200), spec(201)], -1200)
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
    pc.replaceRitesBatch(0, [spec(200)], -600)
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

describe("live Murk emulation (murkAdjustment / effectiveMurks)", () => {
  const spec = (realId: number, effects = [1, 2, 3]) => ({
    real_id: realId,
    item_id: realId + 0x80000000,
    effects,
    curses: [4294967295, 4294967295, 4294967295],
    name: `Relic ${realId}`,
    color: "Red",
    tier: "Grand",
    isDeep: false,
    oddsSource: "exact",
  })

  it("is zero on a clean slot and passes save Murk through", async () => {
    const pc = await freshStore()
    expect(pc.murkAdjustment(pc.readSlot(0))).toBe(0)
    expect(pc.effectiveMurks(100_000, pc.readSlot(0))).toBe(100_000)
    expect(pc.effectiveMurks(null, pc.readSlot(0))).toBeNull()
    expect(pc.effectiveMurks(undefined, pc.readSlot(0))).toBeNull()
  })

  it("a staged rites batch spends the wallet down (the reported bug)", async () => {
    // User journey: spend 87,600 Murk in rites, stage the keepers, come back —
    // every surface must show the spent-down wallet, not the save's raw value.
    const pc = await freshStore()
    pc.replaceRitesBatch(0, [spec(200), spec(201)], -87_600)
    expect(pc.murkAdjustment(pc.readSlot(0))).toBe(-87_600)
    expect(pc.effectiveMurks(100_000, pc.readSlot(0))).toBe(12_400)
  })

  it("an all-dud batch spends the wallet down with zero keepers", async () => {
    // The other reported bug: rolling a batch with no matches must still cost
    // its buy/sell spread — a free preview would be save-scumming.
    const pc = await freshStore()
    pc.replaceRitesBatch(0, [], -4_150)
    expect(pc.murkAdjustment(pc.readSlot(0))).toBe(-4_150)
    expect(pc.effectiveMurks(100_000, pc.readSlot(0))).toBe(95_850)
  })

  it("staged sells credit their cached refund", async () => {
    const pc = await freshStore()
    pc.toggleSell(0, 11, { name: "R1", murk: 150 })
    pc.toggleSell(0, 12, { name: "R2", murk: 550 })
    expect(pc.murkAdjustment(pc.readSlot(0))).toBe(700)
    expect(pc.effectiveMurks(1_000, pc.readSlot(0))).toBe(1_700)
  })

  it("mints + sells combine exactly like the export chain", async () => {
    // Export applies sells first (credit), then the mint delta — the live
    // number must equal what the exported save will actually hold.
    const pc = await freshStore()
    pc.replaceRitesBatch(0, [spec(200)], -1_200)
    pc.toggleSell(0, 11, { name: "R1", murk: 350 })
    expect(pc.murkAdjustment(pc.readSlot(0))).toBe(-850)
    expect(pc.effectiveMurks(10_000, pc.readSlot(0))).toBe(9_150)
  })

  it("a sell without a cached murk label counts as zero", async () => {
    const pc = await freshStore()
    pc.toggleSell(0, 11, { name: "R1" })
    expect(pc.murkAdjustment(pc.readSlot(0))).toBe(0)
  })

  it("removing a mint credits its sell value; the batch loss stands", async () => {
    const pc = await freshStore()
    pc.replaceRitesBatch(0, [spec(200), spec(201)], -1_200)
    const first = pc.readSlot(0).mints[0]
    pc.removeMint(0, first.id)
    // 3-effect normal relic sells for 550 (faithful buy-then-sell).
    expect(pc.murkAdjustment(pc.readSlot(0))).toBe(-1_200 + 550)
    pc.removeMint(0, pc.readSlot(0).mints[0].id)
    // No mints left -> the batch was still bought; only the spread remains.
    expect(pc.murkAdjustment(pc.readSlot(0))).toBe(-1_200 + 550 + 550)
  })

  it("a re-run replaces the batch's delta rather than accumulating", async () => {
    const pc = await freshStore()
    pc.replaceRitesBatch(0, [spec(200)], -600)
    pc.replaceRitesBatch(0, [spec(201)], -1_800)
    expect(pc.murkAdjustment(pc.readSlot(0))).toBe(-1_800)
  })

  it("clamps to the save field's range [0, u32]", async () => {
    const pc = await freshStore()
    pc.replaceRitesBatch(0, [spec(200)], -1_200)
    expect(pc.effectiveMurks(500, pc.readSlot(0))).toBe(0)
    pc.toggleSell(1, 11, { name: "R1", murk: 550 })
    expect(pc.effectiveMurks(0xffffffff, pc.readSlot(1))).toBe(0xffffffff)
  })

  it("tracks slots independently", async () => {
    const pc = await freshStore()
    pc.replaceRitesBatch(0, [spec(200)], -600)
    expect(pc.murkAdjustment(pc.readSlot(1))).toBe(0)
    expect(pc.effectiveMurks(5_000, pc.readSlot(1))).toBe(5_000)
  })
})

describe("effective-state composition selectors", () => {
  const spec = (realId: number) => ({
    real_id: realId,
    item_id: realId + 0x80000000,
    effects: [1, 2, 4294967295],
    curses: [4294967295, 4294967295, 4294967295],
    name: `Relic ${realId}`,
    color: "Blue",
    tier: "Polished",
    isDeep: true,
    oddsSource: "exact",
  })

  it("effectiveRelicRows drops staged sells and appends mint rows", async () => {
    const pc = await freshStore()
    pc.toggleSell(0, 111)
    pc.replaceRitesBatch(0, [spec(300)], -1800)
    const base = [
      { ga_handle: 111, name: "Sold" },
      { ga_handle: 222, name: "Kept" },
    ]
    const rows = pc.effectiveRelicRows(base, pc.readSlot(0))
    expect(rows.map((r) => r.ga_handle)).toEqual([
      222,
      pc.readSlot(0).mints[0].handle,
    ])
    const mintRow = rows[1] as import("./pendingChanges").StagedRelicRow
    expect(mintRow).toMatchObject({
      real_id: 300,
      name: "Relic 300",
      color: "Blue",
      is_deep: true,
      effect_1: 1,
      effect_2: 2,
      effect_3: 4294967295,
      incoming: true,
    })
  })

  it("stagedMintByHandle resolves across slots", async () => {
    const pc = await freshStore()
    pc.replaceRitesBatch(2, [spec(300)], -1800)
    const handle = pc.readSlot(2).mints[0].handle
    expect(pc.stagedMintByHandle(handle)?.real_id).toBe(300)
    expect(pc.stagedMintByHandle(-999_999)).toBeUndefined()
  })

  it("bucketLoadoutOps buckets every op kind", async () => {
    const pc = await freshStore()
    pc.addLoadoutOp(0, {
      kind: "rename",
      index: 1,
      name: "New",
      oldName: "Old",
    })
    pc.addLoadoutOp(0, { kind: "delete", index: 2, name: "Doomed" })
    pc.addLoadoutOp(0, {
      kind: "overwrite",
      index: 3,
      character: "Wylder",
      vessel_id: 7,
      ga_handles: [1, 2, 3],
      targetName: "Target",
    })
    pc.addLoadoutOp(0, {
      kind: "add",
      character: "Wylder",
      vessel_id: 8,
      ga_handles: [4],
      name: "Fresh",
    })
    pc.addLoadoutOp(0, { kind: "reset_vessels" })
    const b = pc.bucketLoadoutOps(pc.readSlot(0))
    expect(b.renameByIndex.get(1)?.name).toBe("New")
    expect(b.deleteByIndex.has(2)).toBe(true)
    expect(b.overwriteByIndex.get(3)?.ga_handles).toEqual([1, 2, 3])
    expect(b.adds.map((a) => a.name)).toEqual(["Fresh"])
    expect(b.resetVesselsId).toBeDefined()
    expect(b.resetPresetsId).toBeUndefined()
  })
})

describe("replace-loadout targets (live preset list)", () => {
  const EXISTING = [
    { index: 0, name: "Keep Me" },
    { index: 1, name: "Doomed" },
    { index: 2, name: "Old Name" },
  ]

  it("excludes staged deletes and applies staged renames", async () => {
    const pc = await freshStore()
    pc.addLoadoutOp(0, { kind: "delete", index: 1, name: "Doomed" })
    pc.addLoadoutOp(0, {
      kind: "rename",
      index: 2,
      name: "New Name",
      oldName: "Old Name",
    })
    const t = pc.replaceTargets(EXISTING, pc.readSlot(0), "Wylder")
    expect(t).toEqual([
      {
        kind: "existing",
        index: 0,
        name: "Keep Me",
        staleOverwriteId: undefined,
      },
      {
        kind: "existing",
        index: 2,
        name: "New Name",
        staleOverwriteId: undefined,
      },
    ])
  })

  it("a staged full reset leaves only staged adds as targets", async () => {
    const pc = await freshStore()
    pc.addLoadoutOp(0, { kind: "reset_presets" })
    pc.addLoadoutOp(0, {
      kind: "add",
      character: "Wylder",
      vessel_id: 5,
      ga_handles: [9],
      name: "Post-reset",
    })
    pc.addLoadoutOp(0, {
      kind: "add",
      character: "Raider",
      vessel_id: 6,
      ga_handles: [8],
      name: "Other Hero",
    })
    const t = pc.replaceTargets(EXISTING, pc.readSlot(0), "Wylder")
    expect(t).toHaveLength(1)
    expect(t[0]).toMatchObject({ kind: "staged-add", name: "Post-reset" })
  })

  it("queueReplaceLoadout supersedes an earlier overwrite of the same preset", async () => {
    const pc = await freshStore()
    pc.addLoadoutOp(0, {
      kind: "overwrite",
      index: 2,
      character: "Wylder",
      vessel_id: 1,
      ga_handles: [1],
      targetName: "Old Name",
    })
    const [t] = pc
      .replaceTargets(EXISTING, pc.readSlot(0), "Wylder")
      .filter((x) => x.kind === "existing" && x.index === 2)
    pc.queueReplaceLoadout(0, t, {
      character: "Wylder",
      vessel_id: 42,
      ga_handles: [7, 8, 9],
      vesselName: "Vessel",
    })
    const ops = pc.readSlot(0).loadoutOps
    // ONE overwrite for index 2 — the second replace supersedes the first.
    expect(ops).toHaveLength(1)
    expect(ops[0]).toMatchObject({
      kind: "overwrite",
      index: 2,
      vessel_id: 42,
      ga_handles: [7, 8, 9],
    })
  })

  it("queueReplaceLoadout swaps a staged add in place, keeping its name", async () => {
    const pc = await freshStore()
    pc.addLoadoutOp(0, {
      kind: "add",
      character: "Wylder",
      vessel_id: 1,
      ga_handles: [1, 2],
      name: "My Setup",
    })
    const [t] = pc.replaceTargets([], pc.readSlot(0), "Wylder")
    expect(t).toMatchObject({ kind: "staged-add", name: "My Setup" })
    pc.queueReplaceLoadout(0, t, {
      character: "Wylder",
      vessel_id: 99,
      ga_handles: [5, 6],
      vesselName: "Better Vessel",
    })
    const ops = pc.readSlot(0).loadoutOps
    expect(ops).toHaveLength(1)
    expect(ops[0]).toMatchObject({
      kind: "add",
      name: "My Setup",
      vessel_id: 99,
      ga_handles: [5, 6],
    })
  })
})

describe("live loadout list (effectiveLoadouts)", () => {
  const SAVED = [
    {
      index: 0,
      character: "Wylder",
      name: "Fire",
      vessel_id: 10,
      ga_handles: [1, 2, 3],
    },
    {
      index: 1,
      character: "Duchess",
      name: "Dregs Raider",
      vessel_id: 20,
      ga_handles: [4, 5, 6],
    },
  ]

  it("passes the save's presets through untouched when nothing is staged", async () => {
    const pc = await freshStore()
    expect(pc.effectiveLoadouts(SAVED, pc.readSlot(0))).toEqual(SAVED)
  })

  it("drops staged deletes and applies staged renames", async () => {
    const pc = await freshStore()
    pc.addLoadoutOp(0, { kind: "delete", index: 0, name: "Fire" })
    pc.addLoadoutOp(0, {
      kind: "rename",
      index: 1,
      name: "Dregs v2",
      oldName: "Dregs Raider",
    })
    const live = pc.effectiveLoadouts(SAVED, pc.readSlot(0))
    expect(live).toHaveLength(1)
    expect(live[0]).toMatchObject({
      index: 1,
      name: "Dregs v2",
      ga_handles: [4, 5, 6],
    })
  })

  it("applies a staged overwrite's vessel AND relics in place", async () => {
    // Replacing a preset from the optimizer can move it to a different vessel;
    // matching the old vessel would report the wrong setup as saved.
    const pc = await freshStore()
    pc.addLoadoutOp(0, {
      kind: "overwrite",
      index: 0,
      character: "Wylder",
      vessel_id: 99,
      ga_handles: [7, 8, 9],
      targetName: "Fire",
    })
    const live = pc.effectiveLoadouts(SAVED, pc.readSlot(0))
    expect(live[0]).toMatchObject({
      index: 0,
      name: "Fire",
      vessel_id: 99,
      ga_handles: [7, 8, 9],
    })
  })

  it("lists a not-yet-exported saved setup as a real loadout", async () => {
    // The whole reason this reads through the staged layer: "Save as loadout"
    // stages an add, and the build card must count it as saved.
    const pc = await freshStore()
    pc.addLoadoutOp(0, {
      kind: "add",
      character: "Duchess",
      vessel_id: 30,
      ga_handles: [11, 12, 13],
      name: "Brand New",
    })
    const live = pc.effectiveLoadouts(SAVED, pc.readSlot(0))
    expect(live).toHaveLength(3)
    expect(live[2]).toEqual({
      index: -1,
      character: "Duchess",
      name: "Brand New",
      vessel_id: 30,
      ga_handles: [11, 12, 13],
    })
  })

  it("a staged full reset leaves only the staged adds", async () => {
    const pc = await freshStore()
    pc.addLoadoutOp(0, { kind: "reset_presets" })
    pc.addLoadoutOp(0, {
      kind: "add",
      character: "Wylder",
      vessel_id: 30,
      ga_handles: [11],
      name: "Survivor",
    })
    const live = pc.effectiveLoadouts(SAVED, pc.readSlot(0))
    expect(live).toHaveLength(1)
    expect(live[0]).toMatchObject({ name: "Survivor", index: -1 })
  })

  it("tolerates a preset with no relic list", async () => {
    const pc = await freshStore()
    const live = pc.effectiveLoadouts(
      [{ index: 0, character: "Wylder", name: "Empty", vessel_id: 1 }],
      pc.readSlot(0),
    )
    expect(live[0].ga_handles).toEqual([])
  })
})

/**
 * The optimizer's "Saved" badge asks "does this setup exist in my game?" — it
 * must ask the LIVE preset list, not the save's. These pair effectiveLoadouts
 * with the matcher to pin the four ways the raw list lied.
 */
describe("saved-loadout badge over the live list", () => {
  const SAVED = [
    {
      index: 0,
      character: "Wylder",
      name: "Fire",
      vessel_id: 10,
      ga_handles: [1, 2, 3],
    },
  ]
  const assigned = [1, 2, 3].map((h) => ({
    ga_handle: h,
    real_id: 100 + h,
    effects: [h, 0, 0],
    curses: [0, 0, 0],
  }))
  const contentMap = new Map(
    assigned.map((r) => [r.ga_handle, relicContentKey(r)]),
  )
  const match = (pc: PendingModule) =>
    findSavedLoadoutMatch(
      10,
      assigned,
      pc.effectiveLoadouts(SAVED, pc.readSlot(0)),
      contentMap,
    )

  it("matches the saved preset when nothing is staged", async () => {
    const pc = await freshStore()
    expect(match(pc)?.loadout.name).toBe("Fire")
  })

  it("shows the staged NEW name after a rename", async () => {
    const pc = await freshStore()
    pc.addLoadoutOp(0, {
      kind: "rename",
      index: 0,
      name: "Ice",
      oldName: "Fire",
    })
    expect(match(pc)?.loadout.name).toBe("Ice")
  })

  it("stops matching a staged-deleted preset", async () => {
    const pc = await freshStore()
    pc.addLoadoutOp(0, { kind: "delete", index: 0, name: "Fire" })
    expect(match(pc)).toBeUndefined()
  })

  it("matches a setup saved from the optimizer but not yet exported", async () => {
    const pc = await freshStore()
    pc.addLoadoutOp(0, { kind: "delete", index: 0, name: "Fire" })
    pc.addLoadoutOp(0, {
      kind: "add",
      character: "Wylder",
      vessel_id: 10,
      ga_handles: [1, 2, 3],
      name: "Fresh",
    })
    const m = match(pc)
    expect(m?.loadout.name).toBe("Fresh")
    // index -1 is what the badge reads to say "not exported yet".
    expect(m?.loadout.index).toBe(-1)
  })

  it("follows a staged overwrite to the new relic set", async () => {
    const pc = await freshStore()
    pc.addLoadoutOp(0, {
      kind: "overwrite",
      index: 0,
      character: "Wylder",
      vessel_id: 10,
      ga_handles: [9, 9, 9],
      targetName: "Fire",
    })
    expect(match(pc)).toBeUndefined()
  })
})
