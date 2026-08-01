/**
 * Upload divergence gate: decides what happens to un-exported staged edits
 * when a new save file is about to replace the one they were made against.
 *
 * Content fingerprints are the only relic identity that survives a re-upload
 * (the game renumbers every ga_handle on save), so the gate compares content-
 * triple counts between the app's current inventory (the OLD save's parse) and
 * the file being uploaded (via POST /saves/inspect, which persists nothing):
 *
 * - every staged mint's content must have entered the new save, and
 * - every staged sell's content must have left it.
 *
 * All verifiable and satisfied → the user exported, imported, and played on:
 * the staged diff is COMMITTED and clears silently. Anything unverifiable
 * (missing fingerprint, missing slot, inspect failure, nothing checkable) or
 * violated → DIVERGENT: warn before discarding. Over-warning is the safe
 * direction — staged work must never be wiped silently.
 */
import { SavesService } from "@/client"
import { isLoggedIn } from "@/hooks/useAuth"
import type { SlotPending } from "./pendingChanges"

export type ContentTriple = {
  real_id: number
  effects: number[]
  curses: number[]
}

const EMPTY = 4294967295

/** Canonical content-fingerprint key: real_id + 3 effects + 3 curses. */
export function fpKey(
  realId: number,
  effects: number[],
  curses: number[],
): string {
  const pad = (a: number[]) => [...a, EMPTY, EMPTY, EMPTY].slice(0, 3)
  return [realId, ...pad(effects), ...pad(curses)].join(",")
}

function counter(keys: Iterable<string>): Map<string, number> {
  const m = new Map<string, number>()
  for (const k of keys) m.set(k, (m.get(k) ?? 0) + 1)
  return m
}

/**
 * True iff this slot's staged sells/mints are all provably reflected in the
 * new save. Multiset inequalities (not equalities) because the user may have
 * kept playing: gaining extra copies of a minted content or selling further
 * copies of a sold one must not flip a committed slot back to divergent.
 */
export function slotCommitted(
  s: SlotPending,
  oldRelics: ContentTriple[] | undefined,
  newRelics: ContentTriple[] | undefined,
): boolean {
  if (!oldRelics || !newRelics) return false
  // Nothing content-verifiable (favorites/loadout-only diffs) → can't prove
  // commitment; ambiguity gates.
  if (s.sells.length === 0 && s.mints.length === 0) return false

  const soldKeys: string[] = []
  for (const h of s.sells) {
    const fp = s.meta[h]?.fp
    if (!fp || fp.length !== 7) return false // legacy sell without fingerprint
    soldKeys.push(fp.join(","))
  }
  const oldC = counter(
    oldRelics.map((r) => fpKey(r.real_id, r.effects, r.curses)),
  )
  const newC = counter(
    newRelics.map((r) => fpKey(r.real_id, r.effects, r.curses)),
  )
  const soldC = counter(soldKeys)
  const mintC = counter(
    s.mints.map((m) => fpKey(m.real_id, m.effects, m.curses)),
  )

  // Every minted content must have entered (net of same-content sells)…
  for (const [k, n] of mintC) {
    const expected = (oldC.get(k) ?? 0) + n - (soldC.get(k) ?? 0)
    if ((newC.get(k) ?? 0) < expected) return false
  }
  // …and every sold content must have left (net of same-content mints).
  for (const [k, n] of soldC) {
    const expected = (oldC.get(k) ?? 0) - n + (mintC.get(k) ?? 0)
    if ((newC.get(k) ?? 0) > expected) return false
  }
  return true
}

export function classifyPending(
  pending: Record<number, SlotPending>,
  oldBySlot: Map<number, ContentTriple[]>,
  newBySlot: Map<number, ContentTriple[]>,
): { committed: number[]; divergent: number[] } {
  const committed: number[] = []
  const divergent: number[] = []
  for (const [slotStr, s] of Object.entries(pending)) {
    const slot = Number(slotStr)
    if (slotCommitted(s, oldBySlot.get(slot), newBySlot.get(slot))) {
      committed.push(slot)
    } else {
      divergent.push(slot)
    }
  }
  return { committed, divergent }
}

/** Content triples of the app's CURRENT inventory for the given slots (the
 *  old save's parse): server relics for signed-in users, the sessionStorage
 *  parse for anonymous ones. Missing slots are simply absent from the map. */
export async function fetchCurrentInventories(
  slots: number[],
): Promise<Map<number, ContentTriple[]>> {
  const out = new Map<number, ContentTriple[]>()
  if (isLoggedIn()) {
    const profiles = await SavesService.listProfiles()
    for (const p of profiles.data ?? []) {
      if (!slots.includes(p.slot_index)) continue
      const relics = await SavesService.getProfileRelics({ profileId: p.id })
      out.set(
        p.slot_index,
        (relics.data ?? []).map((r) => ({
          real_id: r.real_id,
          effects: [r.effect_1, r.effect_2, r.effect_3],
          curses: [r.curse_1, r.curse_2, r.curse_3],
        })),
      )
    }
    return out
  }
  const parsed: Array<Record<string, any>> = JSON.parse(
    sessionStorage.getItem("parsedProfiles") ?? "[]",
  )
  for (const p of parsed) {
    if (!slots.includes(Number(p.slot_index))) continue
    out.set(
      Number(p.slot_index),
      ((p.relics ?? []) as Array<Record<string, any>>).map((r) => ({
        real_id: Number(r.real_id),
        effects: [r.effect_1, r.effect_2, r.effect_3],
        curses: [r.curse_1, r.curse_2, r.curse_3],
      })),
    )
  }
  return out
}

/** Parse the file being uploaded WITHOUT persisting anything. */
export async function inspectNewSave(
  file: File,
): Promise<Map<number, ContentTriple[]>> {
  const res = await SavesService.inspectSave({ formData: { file } })
  const out = new Map<number, ContentTriple[]>()
  for (const slot of res.slots) {
    out.set(slot.slot_index, slot.relics)
  }
  return out
}
