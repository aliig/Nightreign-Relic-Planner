import { useQueries, useQuery } from "@tanstack/react-query"

import { BuildsService, OptimizeService } from "@/client"

/** A build whose optimal layout uses a given relic (for the inventory UI). */
export type RelicBuildRef = { id: string; name: string }

/**
 * Build a usage map: relic real_id -> the user's builds whose optimal layout
 * (for this profile) includes that relic.
 *
 * Powers the inventory status badges (Unused / On the bench / …), the
 * "which builds use this" tooltip, the sell-impact warning, and usage sorting.
 * Keyed by real_id (stable across saves); a build appears once even if the relic
 * fills several of its vessels. Returns an empty map for anonymous users (no
 * persisted builds/snapshots), so usage-derived UI degrades to "Unused".
 */
export function useRelicUsage(profileId: string | null): {
  usage: Map<number, RelicBuildRef[]>
  isLoading: boolean
} {
  const { data: builds } = useQuery({
    queryKey: ["builds"],
    queryFn: () => BuildsService.listBuilds(),
    staleTime: 5 * 60 * 1000,
    enabled: !!profileId,
  })

  const buildList = builds?.data ?? []

  const snapshots = useQueries({
    queries: buildList.map((b) => ({
      queryKey: ["snapshot", b.id, profileId],
      queryFn: () =>
        OptimizeService.getSnapshot({ buildId: b.id, profileId: profileId! }),
      staleTime: Number.POSITIVE_INFINITY,
      enabled: !!profileId,
    })),
  })

  const usage = new Map<number, RelicBuildRef[]>()
  // snapshots[i] corresponds to buildList[i] (useQueries preserves order).
  buildList.forEach((b, i) => {
    const results = snapshots[i]?.data?.results
    if (!results) return
    // Distinct real_ids this build uses across all its vessels.
    const realIds = new Set<number>()
    for (const vessel of results) {
      for (const a of vessel.assignments) {
        if (a.relic?.real_id != null) realIds.add(a.relic.real_id)
      }
    }
    for (const id of realIds) {
      const arr = usage.get(id)
      if (arr) arr.push({ id: b.id, name: b.name })
      else usage.set(id, [{ id: b.id, name: b.name }])
    }
  })

  const isLoading = !!profileId && snapshots.some((s) => s.isLoading)
  return { usage, isLoading }
}
