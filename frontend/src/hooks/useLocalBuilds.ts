import { useState } from "react"

import { BuildsService } from "@/client"

const STORAGE_KEY = "anon_builds"
export const MIGRATION_FLAG = "migrate_builds_on_login"

export interface LocalBuild {
  id: string
  name: string
  character: string
  tiers: Record<string, number[]>
  family_tiers: Record<string, unknown>
  include_deep: boolean
  curse_max: number
  tier_weights?: Record<string, number> | null
  pinned_relics?: number[]
  created_at: string
  updated_at: string
}

function loadFromStorage(): LocalBuild[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as LocalBuild[]) : []
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
      tiers: {},
      family_tiers: {},
      include_deep: false,
      curse_max: 1,
      created_at: now,
      updated_at: now,
    }
    const next = [...builds, newBuild]
    setBuilds(next)
    saveToStorage(next)
    return newBuild
  }

  function update(id: string, patch: Partial<Omit<LocalBuild, "id" | "created_at">>): void {
    const next = builds.map((b) =>
      b.id === id ? { ...b, ...patch, updated_at: new Date().toISOString() } : b,
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

  function createFull(data: Omit<LocalBuild, "id" | "created_at" | "updated_at">): LocalBuild {
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
        Object.values(build.tiers).some((ids) => ids.length > 0) ||
        build.include_deep !== false ||
        build.curse_max !== 1
      if (hasCustomSettings) {
        await BuildsService.updateBuild({
          buildId: created.id,
          requestBody: {
            tiers: build.tiers,
            include_deep: build.include_deep,
            curse_max: build.curse_max,
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
