/**
 * In-game relic storage cap. The game refuses to hold more than this many
 * relics per character, so the inventory shows owned-vs-cap to help players
 * sell down. Source: community-reported limit.
 */
export const RELIC_CAP = 1950

/** Normalized relic row shared by the authenticated + anonymous inventory views. */
export type ManagedRelic = {
  key: string // unique row key (DB id for auth, ga_handle for anon)
  gaHandle: number
  realId: number
  name: string
  color: string
  tier: string
  isDeep: boolean
  effects: number[]
  curses: number[]
  isFavorite: boolean
  equipped: boolean
  /** In-game acquisition counter (higher = acquired later). Null for relics
   *  persisted before the field existed — populated on next save upload. */
  acquisitionId: number | null
}

/**
 * True if a relic is "unique" (one-of-a-kind, cannot be re-acquired once gone).
 * Mirrors `is_unique_relic()` in nrplanner/constants.py — keep the ranges in sync.
 */
export function isUniqueRelic(realId: number): boolean {
  return (
    (realId >= 1000 && realId <= 2100) || (realId >= 10000 && realId <= 19999)
  )
}
