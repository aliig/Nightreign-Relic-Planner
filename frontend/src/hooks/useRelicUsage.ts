import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { useMemo } from "react"

import { type BuildUsageInfo, OptimizeService, type RelicUsage } from "@/client"
import { usePendingSlot } from "@/lib/pendingChanges"

export type { BuildUsageInfo, RelicUsage }

/**
 * Which builds use each relic, and how disposable it is — one request for the
 * whole inventory (POST /optimize/relic-usage).
 *
 * Keyed by ga_handle, i.e. per PHYSICAL relic. The old map was keyed by
 * real_id, the relic TYPE, so up to twelve distinct relics shared one verdict
 * and eleven sellable copies hid behind one that a build actually placed.
 *
 * The query key deliberately depends on staged MINTS only, never on staged
 * sells. A sell changes on every trash click; including it made all ~77
 * per-build queries miss at once, which blanked the map mid-flight and made
 * the "not in a build" filter match nearly the whole inventory. A mint, by
 * contrast, has no save row at all — leave it out and a relic the user just
 * bought reads as owned by nobody. `keepPreviousData` covers the mint case, so
 * the map never blanks even while a new answer is in flight.
 *
 * Anonymous users get `isKnown: false` and an empty map: there are no builds
 * or snapshots to consult, and reporting that as "nothing uses this" is the
 * lie this hook exists to stop telling.
 */
export function useRelicUsage(
  profileId: string | null,
  slotIndex?: number | null,
): {
  byHandle: Map<number, RelicUsage>
  buildsById: Map<string, BuildUsageInfo>
  staleCount: number
  neverOptimizedCount: number
  isKnown: boolean
} {
  const pending = usePendingSlot(slotIndex ?? null)
  const mints = pending.mints
  // Mint identity, sells excluded — see the note above.
  const mintsKey = useMemo(() => mints.map((m) => m.handle).join(","), [mints])

  const { data } = useQuery({
    queryKey: ["relic-usage", profileId, mintsKey],
    queryFn: () =>
      OptimizeService.listRelicUsage({
        requestBody: {
          profile_id: profileId!,
          staged_mints: mints.map((m) => ({
            handle: m.handle,
            real_id: m.real_id,
            effects: m.effects,
            curses: m.curses,
          })),
        },
      }),
    enabled: !!profileId,
    staleTime: 5 * 60 * 1000,
    placeholderData: keepPreviousData,
  })

  // Memoized: these maps are dependencies of the inventory's filter+sort memo,
  // so rebuilding them every render re-filtered and re-sorted 2,000 relics
  // every render.
  return useMemo(() => {
    const byHandle = new Map<number, RelicUsage>()
    const buildsById = new Map<string, BuildUsageInfo>()
    for (const r of data?.relics ?? []) byHandle.set(r.ga_handle, r)
    for (const b of data?.builds ?? []) buildsById.set(b.build_id, b)
    const builds = data?.builds ?? []
    return {
      byHandle,
      buildsById,
      staleCount: builds.filter((b) => !b.fresh && b.optimized).length,
      neverOptimizedCount: builds.filter((b) => !b.optimized).length,
      isKnown: !!profileId && data !== undefined,
    }
  }, [data, profileId])
}
