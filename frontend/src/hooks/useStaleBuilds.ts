import { useQuery } from "@tanstack/react-query"
import { useEffect, useMemo, useState } from "react"

import { OptimizeService, SavesService } from "@/client"
import { stagedFields, stagedKey, usePendingSlot } from "@/lib/pendingChanges"

/**
 * The profile + staged diff an "against my current relics" read describes.
 *
 * `profileId` is optional because the builds page has no profile picker and
 * defaults to the first profile, the same one its optimize page uses; the
 * inventory page has a picker and passes its choice.
 */
export function useProfileStaged(profileId?: string | null): {
  profileId: string | undefined
  sig: string
  staged: ReturnType<typeof stagedFields>
} {
  const { data: profilesData } = useQuery({
    queryKey: ["profiles"],
    queryFn: () => SavesService.listProfiles(),
    staleTime: 5 * 60 * 1000,
  })
  const list = profilesData?.data ?? []
  const profile = profileId ? list.find((p) => p.id === profileId) : list[0]
  const pending = usePendingSlot(profile?.slot_index ?? null)
  const sig = stagedKey(pending)
  // sig fully determines the staged wire fields, so it's the only real dep.
  // biome-ignore lint/correctness/useExhaustiveDependencies: sig covers pending's staged content
  const staged = useMemo(() => stagedFields(pending), [sig])
  return { profileId: profile?.id, sig, staged }
}

/**
 * Which builds' cached results no longer describe the user's current relics.
 *
 * Server-computed (hash comparisons only, no optimizer), because staleness has
 * exactly one definition and it lives next to the snapshot cache — see
 * POST /optimize/freshness. Keyed on the staged signature so trashing a relic
 * or finishing a Relic Rites batch re-asks.
 *
 * The signature is debounced: trashing a hundred relics one click at a time
 * would otherwise fire a freshness request per click.
 */
export function useStaleBuilds(profileId?: string | null): {
  stale: Set<string>
  known: boolean
} {
  const { profileId: id, sig, staged } = useProfileStaged(profileId)
  // Key AND payload settle together, so the answer always describes the diff
  // its cache key names.
  const [settled, setSettled] = useState({ sig, staged })
  useEffect(() => {
    const t = setTimeout(() => setSettled({ sig, staged }), 500)
    return () => clearTimeout(t)
  }, [sig, staged])

  const { data } = useQuery({
    queryKey: ["build-freshness", id, settled.sig],
    queryFn: () =>
      OptimizeService.listBuildFreshness({
        requestBody: { profile_id: id!, ...settled.staged },
      }),
    enabled: !!id,
    staleTime: 30 * 1000,
  })
  return useMemo(() => {
    const stale = new Set<string>()
    for (const row of data ?? []) if (!row.fresh) stale.add(row.build_id)
    return { stale, known: data !== undefined }
  }, [data])
}
