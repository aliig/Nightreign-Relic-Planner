import { useState } from "react"

import { BuildsService } from "@/client"

const STORAGE_KEY = "anon_builds"
export const MIGRATION_FLAG = "migrate_builds_on_login"

export interface WeightGroup {
  weight: number
  effects: number[]
  families: string[]
}

export const DEFAULT_GROUPS: WeightGroup[] = [
  { weight: 100, effects: [], families: [] },
  { weight: 50, effects: [], families: [] },
  { weight: 25, effects: [], families: [] },
  { weight: 10, effects: [], families: [] },
  { weight: -20, effects: [], families: [] },
]

// Stacking categories excluded by default for new builds.
// 300 = "Changes compatible armament's skill to ..."
// 6630000 = "Dormant Power Helps Discover ..."
const DEFAULT_EXCLUDED_STACKING_CATEGORIES = [300, 6630000]

export interface LocalBuild {
  id: string
  name: string
  character: string
  groups: WeightGroup[]
  required_effects: number[]
  required_families: string[]
  excluded_effects: number[]
  excluded_families: string[]
  include_deep: boolean
  curse_max: number
  default_curse_weight?: number
  pinned_relics?: number[]
  excluded_stacking_categories?: number[]
  effect_limits?: Record<number, number>
  family_limits?: Record<string, number>
  created_at: string
  updated_at: string
}

// ---------------------------------------------------------------------------
// Legacy migration (v1–v4: tiers/family_tiers/tier_weights → new schema)
// ---------------------------------------------------------------------------

const _LEGACY_WEIGHTS: Record<string, number> = {
  preferred: 50,
  nice_to_have: 25,
  bonus: 10,
  avoid: -20,
}

function _migrateFromLegacy(build: Record<string, unknown>): LocalBuild {
  const tiers = (build.tiers as Record<string, number[]>) ?? {}
  const familyTiers = (build.family_tiers as Record<string, string[]>) ?? {}
  const tierWeights = (build.tier_weights as Record<string, number>) ?? {}

  const groups: WeightGroup[] = []
  for (const [key, defaultWeight] of Object.entries(_LEGACY_WEIGHTS)) {
    const effs = tiers[key] ?? []
    const fams = familyTiers[key] ?? []
    if (effs.length > 0 || fams.length > 0) {
      groups.push({
        weight: tierWeights[key] ?? defaultWeight,
        effects: effs,
        families: fams,
      })
    }
  }

  return {
    id: build.id as string,
    name: build.name as string,
    character: build.character as string,
    groups:
      groups.length > 0 ? groups : [...DEFAULT_GROUPS.map((g) => ({ ...g }))],
    required_effects: (tiers.required ?? []) as number[],
    required_families: (familyTiers.required ?? []) as string[],
    excluded_effects: (tiers.blacklist ?? []) as number[],
    excluded_families: (familyTiers.blacklist ?? []) as string[],
    include_deep: (build.include_deep as boolean) ?? false,
    curse_max: (build.curse_max as number) ?? 1,
    pinned_relics: (build.pinned_relics as number[]) ?? [],
    excluded_stacking_categories:
      (build.excluded_stacking_categories as number[]) ?? [],
    created_at: build.created_at as string,
    updated_at: build.updated_at as string,
  }
}

function loadFromStorage(): LocalBuild[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown[]
    return parsed.map((b) => {
      const obj = b as Record<string, unknown>
      if ("tiers" in obj && !("groups" in obj)) {
        return _migrateFromLegacy(obj)
      }
      return obj as LocalBuild
    })
  } catch {
    return []
  }
}

function saveToStorage(builds: LocalBuild[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(builds))
}

export function useLocalBuilds() {
  const [builds, setBuilds] = useState<LocalBuild[]>(() => loadFromStorage())

  function create(data: { name: string; character: string }): LocalBuild {
    const now = new Date().toISOString()
    const newBuild: LocalBuild = {
      id: crypto.randomUUID(),
      name: data.name,
      character: data.character,
      groups: DEFAULT_GROUPS.map((g) => ({ ...g })),
      required_effects: [],
      required_families: [],
      excluded_effects: [],
      excluded_families: [],
      include_deep: false,
      curse_max: 1,
      excluded_stacking_categories: [...DEFAULT_EXCLUDED_STACKING_CATEGORIES],
      created_at: now,
      updated_at: now,
    }
    const next = [...builds, newBuild]
    setBuilds(next)
    saveToStorage(next)
    return newBuild
  }

  function update(
    id: string,
    patch: Partial<Omit<LocalBuild, "id" | "created_at">>,
  ): void {
    const next = builds.map((b) =>
      b.id === id
        ? { ...b, ...patch, updated_at: new Date().toISOString() }
        : b,
    )
    setBuilds(next)
    saveToStorage(next)
  }

  function remove(id: string): void {
    const next = builds.filter((b) => b.id !== id)
    setBuilds(next)
    saveToStorage(next)
  }

  function getById(id: string): LocalBuild | undefined {
    return builds.find((b) => b.id === id)
  }

  function duplicate(id: string): LocalBuild | undefined {
    const source = builds.find((b) => b.id === id)
    if (!source) return undefined
    const now = new Date().toISOString()
    const copy: LocalBuild = {
      ...source,
      id: crypto.randomUUID(),
      name: `${source.name} (Copy)`,
      created_at: now,
      updated_at: now,
    }
    const next = [...builds, copy]
    setBuilds(next)
    saveToStorage(next)
    return copy
  }

  function createFull(
    data: Omit<LocalBuild, "id" | "created_at" | "updated_at">,
  ): LocalBuild {
    const now = new Date().toISOString()
    const newBuild: LocalBuild = {
      ...data,
      id: crypto.randomUUID(),
      created_at: now,
      updated_at: now,
    }
    const next = [...builds, newBuild]
    setBuilds(next)
    saveToStorage(next)
    return newBuild
  }

  return { builds, create, update, remove, getById, duplicate, createFull }
}

/**
 * Called after login when MIGRATION_FLAG is set in sessionStorage (i.e. user
 * just signed up in the same tab session). Pushes each local build to the API,
 * clears localStorage, and returns the count of successfully migrated builds.
 */
export async function migrateLocalBuildsToDb(): Promise<number> {
  const builds = loadFromStorage()
  if (!builds.length) return 0

  const results = await Promise.allSettled(
    builds.map(async (build) => {
      const created = await BuildsService.createBuild({
        requestBody: { name: build.name, character: build.character },
      })
      const hasCustomSettings =
        (build.groups ?? []).some(
          (g) => g.effects.length > 0 || g.families.length > 0,
        ) ||
        (build.required_effects ?? []).length > 0 ||
        (build.excluded_effects ?? []).length > 0 ||
        (build.excluded_stacking_categories ?? []).length > 0 ||
        Object.keys(build.effect_limits ?? {}).length > 0 ||
        Object.keys(build.family_limits ?? {}).length > 0 ||
        build.include_deep !== false ||
        build.curse_max !== 1 ||
        (build.default_curse_weight ?? 0) !== 0
      if (hasCustomSettings) {
        await BuildsService.updateBuild({
          buildId: created.id,
          requestBody: {
            groups: build.groups,
            required_effects: build.required_effects,
            required_families: build.required_families,
            excluded_effects: build.excluded_effects,
            excluded_families: build.excluded_families,
            excluded_stacking_categories: build.excluded_stacking_categories,
            effect_limits: build.effect_limits ?? {},
            family_limits: build.family_limits ?? {},
            include_deep: build.include_deep,
            curse_max: build.curse_max,
            default_curse_weight: build.default_curse_weight ?? 0,
          },
        })
      }
    }),
  )

  // Clear local builds regardless of partial failures — the user is now
  // authenticated and any failed builds can be recreated from scratch.
  localStorage.removeItem(STORAGE_KEY)

  return results.filter((r) => r.status === "fulfilled").length
}
