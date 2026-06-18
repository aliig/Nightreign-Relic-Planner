/**
 * Working diff of edits the user has made to their save, shared across pages.
 *
 * The app treats these edits as already applied to a live, in-memory copy of the
 * save: trashed relics drop out of the inventory, deleted loadouts disappear, new
 * ones show up inline. This store IS that diff (and the change log) — nothing is
 * written to disk until the user exports. Every edit (sell/bookmark a relic,
 * add/delete/rename/overwrite a loadout, reset vessels/loadouts) lives here.
 *
 * Keyed by save-slot index (the selected profile). Backed by sessionStorage so it
 * survives SPA navigation (the save File itself is held separately in saveFile.ts
 * and is re-selected after a full reload).
 */
import { useSyncExternalStore } from "react"

export type PendingLoadoutOp =
  | {
      id: string
      kind: "add"
      character: string
      vessel_id: number
      ga_handles: number[]
      name: string
      vesselName?: string
    }
  | {
      id: string
      kind: "overwrite"
      index: number
      character: string
      vessel_id: number
      ga_handles: number[]
      name?: string
      targetName: string
    }
  | { id: string; kind: "delete"; index: number; name: string }
  | { id: string; kind: "rename"; index: number; name: string; oldName: string }
  | { id: string; kind: "reset_vessels" }
  | { id: string; kind: "reset_presets" }

/** Display label for a relic edit, so the change log can name it without a lookup. */
export type RelicMeta = { name: string; isDeep?: boolean; murk?: number }

export type SlotPending = {
  sells: number[] // ga_handles to sell
  favorites: Record<number, boolean> // ga_handle -> desired bookmark state
  loadoutOps: PendingLoadoutOp[]
  // Label cache keyed by ga_handle (relic name / murk value) for the change log.
  // Purely cosmetic; pruned to the handles still referenced by sells/favorites.
  meta: Record<number, RelicMeta>
}

type State = Record<number, SlotPending>

const STORAGE_KEY = "pendingChanges"

function emptySlot(): SlotPending {
  return { sells: [], favorites: {}, loadoutOps: [], meta: {} }
}

function load(): State {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as State
    // Normalize so every slot has the full shape (sessions saved before `meta`
    // was added would otherwise read back without it and crash raw-state readers).
    const out: State = {}
    for (const [k, v] of Object.entries(parsed)) {
      out[Number(k)] = { ...emptySlot(), ...v }
    }
    return out
  } catch {
    return {}
  }
}

let state: State = load()
const listeners = new Set<() => void>()

function persist() {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state))
  } catch {
    /* ignore quota / disabled storage */
  }
}

function setState(next: State) {
  state = next
  persist()
  for (const l of listeners) l()
}

function subscribe(cb: () => void) {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

function getSlot(state: State, slot: number): SlotPending {
  // Spread defaults so slots restored from older sessionStorage (no `meta`) are safe.
  return { ...emptySlot(), ...state[slot] }
}

/** Drop label-cache entries no longer referenced by a sell or bookmark change. */
function pruneMeta(s: SlotPending): SlotPending {
  const live = new Set<number>([
    ...s.sells,
    ...Object.keys(s.favorites).map(Number),
  ])
  const meta: Record<number, RelicMeta> = {}
  for (const [k, v] of Object.entries(s.meta)) {
    if (live.has(Number(k))) meta[Number(k)] = v
  }
  return { ...s, meta }
}

function updateSlot(slot: number, fn: (s: SlotPending) => SlotPending) {
  const next = { ...state, [slot]: pruneMeta(fn(getSlot(state, slot))) }
  // Drop the slot entry entirely if it became empty (keeps counts clean).
  const s = next[slot]
  if (
    s.sells.length === 0 &&
    Object.keys(s.favorites).length === 0 &&
    s.loadoutOps.length === 0
  ) {
    delete next[slot]
  }
  setState(next)
}

let idCounter = 0
function nextId(): string {
  idCounter += 1
  return `op_${idCounter}_${idCounter * 2654435761}`
}

// --- mutators --------------------------------------------------------------

export function toggleSell(slot: number, gaHandle: number, meta?: RelicMeta) {
  updateSlot(slot, (s) => {
    const has = s.sells.includes(gaHandle)
    return {
      ...s,
      sells: has
        ? s.sells.filter((h) => h !== gaHandle)
        : [...s.sells, gaHandle],
      meta: meta ? { ...s.meta, [gaHandle]: meta } : s.meta,
    }
  })
}

export function setFavorite(
  slot: number,
  gaHandle: number,
  desired: boolean | null,
  meta?: RelicMeta,
) {
  updateSlot(slot, (s) => {
    const favorites = { ...s.favorites }
    if (desired === null) delete favorites[gaHandle]
    else favorites[gaHandle] = desired
    return {
      ...s,
      favorites,
      meta: meta ? { ...s.meta, [gaHandle]: meta } : s.meta,
    }
  })
}

// Distributive Omit so each union member keeps its own fields (a plain
// Omit<union, "id"> collapses to only the shared keys).
type DistributiveOmit<T, K extends PropertyKey> = T extends unknown
  ? Omit<T, K>
  : never

export function addLoadoutOp(
  slot: number,
  op: DistributiveOmit<PendingLoadoutOp, "id">,
): string {
  const id = nextId()
  updateSlot(slot, (s) => ({
    ...s,
    loadoutOps: [...s.loadoutOps, { ...op, id } as PendingLoadoutOp],
  }))
  return id
}

export function removeLoadoutOp(slot: number, id: string) {
  updateSlot(slot, (s) => ({
    ...s,
    loadoutOps: s.loadoutOps.filter((o) => o.id !== id),
  }))
}

export function clearSlot(slot: number) {
  const next = { ...state }
  delete next[slot]
  setState(next)
}

export function clearAll() {
  setState({})
}

// --- selectors / hooks -----------------------------------------------------

/** Reactive snapshot of one slot's pending changes. */
export function usePendingSlot(slot: number | null | undefined): SlotPending {
  const snap = useSyncExternalStore(subscribe, () => state)
  return slot == null ? emptySlot() : getSlot(snap, slot)
}

/** Reactive snapshot of the whole diff across all slots (for the change log). */
export function usePendingAll(): State {
  return useSyncExternalStore(subscribe, () => state)
}

/** Non-reactive read of a slot (for export handlers). */
export function readSlot(slot: number): SlotPending {
  return getSlot(state, slot)
}

export function readAll(): State {
  return state
}
