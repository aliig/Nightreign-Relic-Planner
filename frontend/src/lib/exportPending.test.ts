import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { exportPendingChanges } from "./exportPending"
import type { SlotPending } from "./pendingChanges"

function res(headers: Record<string, string>) {
  return {
    ok: true,
    headers: new Headers({
      "content-disposition": 'attachment; filename="NR0000_edited.sl2"',
      ...headers,
    }),
    blob: async () => new Blob([new Uint8Array([1, 2, 3])]),
  }
}

describe("exportPendingChanges", () => {
  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => "blob:mock")
    URL.revokeObjectURL = vi.fn()
    vi.spyOn(document, "createElement").mockImplementation(
      () =>
        ({ href: "", download: "", click: vi.fn() }) as unknown as HTMLElement,
    )
    localStorage.setItem("access_token", "tok")
  })
  afterEach(() => {
    vi.restoreAllMocks()
    localStorage.clear()
  })

  it("chains relic + loadout exports for a slot and aggregates the summary", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        res({ "x-relics-removed": "2", "x-favorites-changed": "1" }),
      )
      .mockResolvedValueOnce(
        res({ "x-loadouts-added": "1", "x-loadouts-renamed": "3" }),
      )
    vi.stubGlobal("fetch", fetchMock)

    const pending: Record<number, SlotPending> = {
      0: {
        sells: [10, 11],
        favorites: { 12: true },
        loadoutOps: [
          {
            id: "a",
            kind: "add",
            character: "Wylder",
            vessel_id: 1002,
            ga_handles: [],
            name: "x",
          },
          { id: "b", kind: "rename", index: 1, name: "y", oldName: "z" },
        ],
        mints: [],
        murkDelta: 0,
        meta: {},
      },
    }

    const file = new File([new Uint8Array([0])], "NR0000.sl2")
    const summary = await exportPendingChanges(file, pending)

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/saves/export")
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/saves/export-loadouts")
    expect(summary.sold).toBe(2)
    expect(summary.bookmarks).toBe(1)
    expect(summary.added).toBe(1)
    expect(summary.renamed).toBe(3)
    expect(summary.filename).toBe("NR0000_edited.sl2")
  })

  it("runs sells before mints so refunds land before the mint debit", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(res({ "x-relics-removed": "1" }))
      .mockResolvedValueOnce(res({ "x-relics-added": "2" }))
      .mockResolvedValueOnce(res({ "x-loadouts-added": "1" }))
    vi.stubGlobal("fetch", fetchMock)

    const pending: Record<number, SlotPending> = {
      0: {
        sells: [10],
        favorites: {},
        loadoutOps: [
          {
            id: "a",
            kind: "add",
            character: "Wylder",
            vessel_id: 1002,
            ga_handles: [],
            name: "x",
          },
        ],
        mints: [
          {
            id: "m1",
            real_id: 205,
            item_id: 205 + 2147483648,
            effects: [310000, 4294967295, 4294967295],
            curses: [4294967295, 4294967295, 4294967295],
            name: "Minted",
            color: "Red",
            tier: "Delicate",
            isDeep: false,
            oddsSource: "exact",
            builds: [],
          },
        ],
        murkDelta: -450,
        meta: {},
      },
    }

    const summary = await exportPendingChanges(
      new File([new Uint8Array([0])], "s.sl2"),
      pending,
    )

    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual([
      "/api/v1/saves/export",
      "/api/v1/saves/export-add-relics",
      "/api/v1/saves/export-loadouts",
    ])
    const addForm = fetchMock.mock.calls[1][1].body as FormData
    expect(addForm.get("murk_delta")).toBe("-450")
    expect(summary.sold).toBe(1)
    expect(summary.minted).toBe(2)
  })

  it("skips the relic call when only loadout ops are pending", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(res({ "x-loadouts-deleted": "1" }))
    vi.stubGlobal("fetch", fetchMock)
    const pending: Record<number, SlotPending> = {
      2: {
        sells: [],
        favorites: {},
        loadoutOps: [{ id: "d", kind: "delete", index: 0, name: "n" }],
        mints: [],
        murkDelta: 0,
        meta: {},
      },
    }
    const summary = await exportPendingChanges(
      new File([new Uint8Array([0])], "s.sl2"),
      pending,
    )
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/saves/export-loadouts")
    expect(summary.deleted).toBe(1)
  })

  it("throws with the server detail on failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        json: async () => ({ detail: "bad save" }),
      })),
    )
    const pending: Record<number, SlotPending> = {
      0: {
        sells: [1],
        favorites: {},
        loadoutOps: [],
        mints: [],
        murkDelta: 0,
        meta: {},
      },
    }
    await expect(
      exportPendingChanges(new File([new Uint8Array([0])], "s.sl2"), pending),
    ).rejects.toThrow("bad save")
  })
})
