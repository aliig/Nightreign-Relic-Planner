import { useQueries, useQuery } from "@tanstack/react-query"

import { BuildsService, OptimizeService } from "@/client"

/**
 * Build a usage map: relic real_id -> number of the user's builds whose optimal
 * layout (for this profile) includes that relic.
 *
 * Powers the inventory "# setups" column and "unused" sorting. Usage is keyed by
 * real_id (stable across saves); a build counts once even if the relic appears
 * in several of its vessels. Returns an empty map for anonymous users (no
 * persisted builds/snapshots).
 */
export function useRelicUsage(profileId: string | null): {
  usage: Map<number, number>
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

  const usage = new Map<number, number>()
  for (const snap of snapshots) {
    const results = snap.data?.results
    if (!results) continue
    // Distinct real_ids used by this build across all its vessels.
    const realIds = new Set<number>()
    for (const vessel of results) {
      for (const a of vessel.assignments) {
        if (a.relic?.real_id != null) realIds.add(a.relic.real_id)
      }
    }
    for (const id of realIds) {
      usage.set(id, (usage.get(id) ?? 0) + 1)
    }
  }

  const isLoading = !!profileId && snapshots.some((s) => s.isLoading)
  return { usage, isLoading }
}
