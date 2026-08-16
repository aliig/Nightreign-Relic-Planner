import { useQuery } from "@tanstack/react-query"
import { useMemo } from "react"

import { GameService } from "@/client"
import { buildEffectMap } from "@/components/RelicDisplay"

/**
 * Effect ID -> display name map, for surfaces that want to name a relic's
 * effects without owning the fetch.
 *
 * Non-suspense on purpose: change lists, banners and the optimize tracker all
 * render before this resolves, and a relic chip without its effect names is
 * still useful — it just loses the hover detail for a moment. Game data never
 * changes within a session, so the query is cached forever and shared by every
 * caller (same key as the inventory/optimizer pages already use).
 */
export function useEffectMap(): Map<number, string> {
  const { data } = useQuery({
    queryKey: ["game", "effects"],
    queryFn: () => GameService.getEffects(),
    staleTime: Number.POSITIVE_INFINITY,
  })
  return useMemo(() => buildEffectMap((data ?? []) as unknown[]), [data])
}
