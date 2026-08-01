/**
 * Tests for useLocalBuilds hook and migrateLocalBuildsToDb.
 *
 * No network layer — localStorage is the only external dependency,
 * provided by jsdom. BuildsService is mocked at the module level.
 */
import { act, renderHook } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

// vi.mock is hoisted to the top of the file, so variables used inside its
// factory must also be hoisted with vi.hoisted() to avoid reference errors.
const { mockCreateBuild, mockUpdateBuild } = vi.hoisted(() => ({
  mockCreateBuild: vi.fn(),
  mockUpdateBuild: vi.fn(),
}))

vi.mock("@/client", () => ({
  BuildsService: {
    createBuild: mockCreateBuild,
    updateBuild: mockUpdateBuild,
  },
}))

import {
  type LocalBuild,
  migrateLocalBuildsToDb,
  toInlineBuild,
  useLocalBuilds,
} from "./useLocalBuilds"

beforeEach(() => {
  localStorage.clear()
  vi.clearAllMocks()
})

describe("toInlineBuild", () => {
  it("forwards every scoring-relevant field to the inline payload", () => {
    // Regression: the anon optimize form once hand-rolled this payload and
    // silently dropped limits/floors/stacking-categories (and required_*),
    // which the backend then filled with defaults.
    const build: LocalBuild = {
      id: "b1",
      name: "Full Build",
      character: "Wylder",
      groups: [{ weight: 50, effects: [1], families: ["Fam"] }],
      required_effects: [7],
      required_families: ["Req Fam"],
      excluded_effects: [8],
      excluded_families: ["Ex Fam"],
      include_deep: true,
      curse_max: 2,
      default_curse_weight: -5,
      pinned_relics: [123],
      excluded_stacking_categories: [300],
      effect_limits: { 1: 2 },
      family_limits: { Fam: 1 },
      family_weight_floors: { Fam: 10 },
      created_at: "2026-01-01",
      updated_at: "2026-01-02",
    }

    expect(toInlineBuild(build)).toEqual({
      id: "b1",
      name: "Full Build",
      character: "Wylder",
      groups: [{ weight: 50, effects: [1], families: ["Fam"] }],
      required_effects: [7],
      required_families: ["Req Fam"],
      excluded_effects: [8],
      excluded_families: ["Ex Fam"],
      include_deep: true,
      curse_max: 2,
      default_curse_weight: -5,
      pinned_relics: [123],
      excluded_stacking_categories: [300],
      effect_limits: { 1: 2 },
      family_limits: { Fam: 1 },
      family_weight_floors: { Fam: 10 },
    })
  })

  it("defaults optional fields instead of omitting them", () => {
    const minimal = {
      id: "b2",
      name: "Minimal",
      character: "Wylder",
      groups: [],
      required_effects: [],
      required_families: [],
      excluded_effects: [],
      excluded_families: [],
      include_deep: false,
      curse_max: 1,
      created_at: "2026-01-01",
      updated_at: "2026-01-01",
    } as LocalBuild

    const inline = toInlineBuild(minimal)
    expect(inline.default_curse_weight).toBe(0)
    expect(inline.pinned_relics).toEqual([])
    expect(inline.excluded_stacking_categories).toEqual([])
    expect(inline.effect_limits).toEqual({})
    expect(inline.family_limits).toEqual({})
    expect(inline.family_weight_floors).toEqual({})
  })
})

describe("useLocalBuilds", () => {
  it("starts with empty builds when localStorage is empty", () => {
    const { result } = renderHook(() => useLocalBuilds())
    expect(result.current.builds).toEqual([])
  })

  it("create() adds a build and persists it to localStorage", () => {
    const { result } = renderHook(() => useLocalBuilds())

    act(() => {
      result.current.create({ name: "Build A", character: "Wylder" })
    })

    expect(result.current.builds).toHaveLength(1)
    expect(result.current.builds[0].name).toBe("Build A")
    expect(result.current.builds[0].character).toBe("Wylder")

    const stored = JSON.parse(localStorage.getItem("anon_builds") ?? "[]")
    expect(stored).toHaveLength(1)
    expect(stored[0].name).toBe("Build A")
  })

  it("update() patches an existing build by id", () => {
    const { result } = renderHook(() => useLocalBuilds())

    let id: string = ""
    act(() => {
      const b = result.current.create({ name: "Original", character: "Wylder" })
      id = b.id
    })

    act(() => {
      result.current.update(id, { name: "Renamed" })
    })

    expect(result.current.builds[0].name).toBe("Renamed")
    // created_at must not change; updated_at should differ
    expect(result.current.builds[0].id).toBe(id)
  })

  it("remove() deletes a build by id", () => {
    const { result } = renderHook(() => useLocalBuilds())

    let id: string = ""
    act(() => {
      const b = result.current.create({ name: "ToDelete", character: "Wylder" })
      id = b.id
    })

    act(() => {
      result.current.remove(id)
    })

    expect(result.current.builds).toHaveLength(0)
    const stored = JSON.parse(localStorage.getItem("anon_builds") ?? "[]")
    expect(stored).toHaveLength(0)
  })

  it("getById() returns undefined for an unknown id", () => {
    const { result } = renderHook(() => useLocalBuilds())
    expect(result.current.getById("nonexistent-id")).toBeUndefined()
  })

  it("survives invalid JSON in localStorage and returns empty array", () => {
    localStorage.setItem("anon_builds", "not-valid-json{{{")
    const { result } = renderHook(() => useLocalBuilds())
    expect(result.current.builds).toEqual([])
  })

  it("hydrates from existing localStorage on mount", () => {
    const existing = [
      {
        id: "abc",
        name: "Persisted",
        character: "Duchess",
        tiers: {},
        family_tiers: {},
        include_deep: false,
        curse_max: 0,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
    ]
    localStorage.setItem("anon_builds", JSON.stringify(existing))

    const { result } = renderHook(() => useLocalBuilds())
    expect(result.current.builds).toHaveLength(1)
    expect(result.current.builds[0].name).toBe("Persisted")
  })
})

describe("migrateLocalBuildsToDb", () => {
  it("calls createBuild for each local build and clears localStorage", async () => {
    const builds = [
      {
        id: "local-1",
        name: "Build 1",
        character: "Wylder",
        tiers: {},
        family_tiers: {},
        include_deep: false,
        curse_max: 1,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
      {
        id: "local-2",
        name: "Build 2",
        character: "Guardian",
        tiers: {},
        family_tiers: {},
        include_deep: false,
        curse_max: 1,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
    ]
    localStorage.setItem("anon_builds", JSON.stringify(builds))
    mockCreateBuild.mockResolvedValue({ id: "server-id" })

    const count = await migrateLocalBuildsToDb()

    expect(count).toBe(2)
    expect(mockCreateBuild).toHaveBeenCalledTimes(2)
    expect(localStorage.getItem("anon_builds")).toBeNull()
  })

  it("returns 0 and does not call API when localStorage is empty", async () => {
    const count = await migrateLocalBuildsToDb()
    expect(count).toBe(0)
    expect(mockCreateBuild).not.toHaveBeenCalled()
  })

  it("clears localStorage even on partial failure", async () => {
    const builds = [
      {
        id: "local-1",
        name: "Build 1",
        character: "Wylder",
        tiers: {},
        family_tiers: {},
        include_deep: false,
        curse_max: 1,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
    ]
    localStorage.setItem("anon_builds", JSON.stringify(builds))
    mockCreateBuild.mockRejectedValue(new Error("Network error"))

    const count = await migrateLocalBuildsToDb()

    expect(count).toBe(0) // 0 succeeded
    expect(localStorage.getItem("anon_builds")).toBeNull() // still cleared
  })
})
