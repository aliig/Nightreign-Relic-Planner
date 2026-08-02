/**
 * Saved-loadout matching for optimizer result cards.
 *
 * The optimizer collapses functionally identical layouts (same relics spread
 * across same-color slots) into one canonical arrangement, so a result can
 * differ from an in-game loadout preset by slot order or by which physical
 * copy of a duplicate relic it picked — while being the exact same build in
 * play. The "Saved" badge must recognize those too, not just handle-for-handle
 * matches. Mirrors the backend's VesselResult.layout_fingerprint semantics
 * (nrplanner/models.py): content, not ga_handle, is the functional identity.
 *
 * Two tiers, exact preferred:
 *  1. exact      — same vessel + same ga_handle set (slot order ignored)
 *  2. equivalent — same vessel + same multiset of relic contents
 *                  (real_id + effects + curses)
 */

export type RelicContent = {
  real_id?: number
  effects?: number[] | null
  curses?: number[] | null
}

/** Canonical relic content key (`real_id:effects:curses`, raw 3-length arrays
 *  with EMPTY padding). Content is the only relic identity stable across
 *  copies and re-uploads — ga_handles are copy-specific and renumbered by the
 *  game on every save. */
export function relicContentKey(r: RelicContent): string {
  return `${r.real_id ?? ""}:${(r.effects ?? []).join(",")}:${(r.curses ?? []).join(",")}`
}

export type SavedLoadoutRef = {
  index: number
  name: string
  vessel_id: number
  ga_handles: number[]
}

export type SavedLoadoutMatch = {
  loadout: SavedLoadoutRef
  /** False for a handle-for-handle match; true when the loadout holds the
   *  same relic contents in a different arrangement (swapped same-color slots
   *  and/or interchangeable duplicate copies). */
  equivalent: boolean
}

function counter(keys: Iterable<string>): Map<string, number> {
  const m = new Map<string, number>()
  for (const k of keys) m.set(k, (m.get(k) ?? 0) + 1)
  return m
}

function countersEqual(
  a: Map<string, number>,
  b: Map<string, number>,
): boolean {
  if (a.size !== b.size) return false
  for (const [k, n] of a) if (b.get(k) !== n) return false
  return true
}

/**
 * Find the in-game loadout this vessel result reproduces, if any.
 *
 * `assignedRelics` is the result's non-empty slot relics. Without
 * `relicContentByHandle` (ga_handle → relicContentKey of the profile's
 * relics) only the exact tier runs; a loadout handle missing from the map
 * makes that loadout unverifiable and it is never content-matched.
 */
export function findSavedLoadoutMatch(
  vesselId: number,
  assignedRelics: Array<RelicContent & { ga_handle?: number }>,
  existing: SavedLoadoutRef[] | undefined,
  relicContentByHandle?: Map<number, string>,
): SavedLoadoutMatch | undefined {
  if (!existing?.length) return undefined
  const sameVessel = existing.filter((e) => e.vessel_id === vesselId)
  if (!sameVessel.length) return undefined

  const mineHandles = new Set(
    assignedRelics.map((r) => r.ga_handle ?? 0).filter((h) => h !== 0),
  )
  const exact = sameVessel.find((e) => {
    const theirs = (e.ga_handles ?? []).filter((h) => h !== 0)
    return (
      theirs.length === mineHandles.size &&
      theirs.every((h) => mineHandles.has(h))
    )
  })
  if (exact) return { loadout: exact, equivalent: false }

  if (!relicContentByHandle) return undefined
  const mine = counter(assignedRelics.map(relicContentKey))
  const equiv = sameVessel.find((e) => {
    const theirs = (e.ga_handles ?? []).filter((h) => h !== 0)
    if (theirs.length !== assignedRelics.length) return false
    const keys: string[] = []
    for (const h of theirs) {
      const k = relicContentByHandle.get(h)
      if (k === undefined) return false
      keys.push(k)
    }
    return countersEqual(counter(keys), mine)
  })
  return equiv ? { loadout: equiv, equivalent: true } : undefined
}
