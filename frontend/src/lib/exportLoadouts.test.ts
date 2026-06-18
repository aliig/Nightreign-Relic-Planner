import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { exportModifiedLoadouts } from "./exportLoadouts"

describe("exportModifiedLoadouts", () => {
  beforeEach(() => {
    // jsdom lacks URL.createObjectURL / revokeObjectURL
    URL.createObjectURL = vi.fn(() => "blob:mock")
    URL.revokeObjectURL = vi.fn()
    // Stub the download anchor so jsdom doesn't attempt real navigation.
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

  it("posts file + slot + operations and parses summary headers", async () => {
    const headers = new Headers({
      "content-disposition": 'attachment; filename="NR0000_edited.sl2"',
      "x-loadouts-added": "1",
      "x-loadouts-deleted": "0",
      "x-loadouts-renamed": "2",
      "x-loadouts-overwritten": "0",
      "x-vessels-reset": "0",
      "x-presets-reset": "0",
      "x-loadouts-used": "60",
    })
    const fetchMock = vi.fn(async () => ({
      ok: true,
      headers,
      blob: async () => new Blob([new Uint8Array([1, 2, 3])]),
    }))
    vi.stubGlobal("fetch", fetchMock)

    const file = new File([new Uint8Array([0])], "NR0000.sl2")
    const result = await exportModifiedLoadouts({
      file,
      slotIndex: 0,
      operations: [
        {
          op: "add",
          character: "Wylder",
          vessel_id: 1002,
          ga_handles: [],
          name: "x",
        },
      ],
    })

    expect(fetchMock).toHaveBeenCalledOnce()
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ]
    expect(url).toBe("/api/v1/saves/export-loadouts")
    expect((init.headers as Record<string, string>).Authorization).toBe(
      "Bearer tok",
    )
    const form = init.body as FormData
    expect(form.get("slot_index")).toBe("0")
    expect(JSON.parse(form.get("operations") as string)).toHaveLength(1)

    expect(result.filename).toBe("NR0000_edited.sl2")
    expect(result.added).toBe(1)
    expect(result.renamed).toBe(2)
    expect(result.used).toBe(60)
  })

  it("throws LoadoutExportError with the server detail on failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        json: async () => ({
          detail: "That would exceed the 100-loadout limit.",
        }),
      })),
    )
    const file = new File([new Uint8Array([0])], "NR0000.sl2")
    await expect(
      exportModifiedLoadouts({
        file,
        slotIndex: 0,
        operations: [{ op: "reset_presets" }],
      }),
    ).rejects.toThrow("100-loadout limit")
  })
})
