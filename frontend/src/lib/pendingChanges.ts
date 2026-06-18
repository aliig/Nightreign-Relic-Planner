/**
 * Global "pending changes" cart for save-file edits, shared across pages.
 *
 * Every edit the user makes (sell/bookmark a relic, add/delete/rename/overwrite a
 * loadout, reset vessels/loadouts) is queued here instead of exporting immediately.
 * The navbar shows the count and exports everything in one go. Lists read this
 * store to render optimistic "pending" badges.
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

export type SlotPending = {
  sells: number[] // ga_handles to sell
  favorites: Record<number, boolean> // ga_handle -> desired bookmark state
  loadoutOps: PendingLoadoutOp[]
}

type State = Record<number, SlotPending>

const STORAGE_KEY = "pendingChanges"

function emptySlot(): SlotPending {
  return { sells: [], favorites: {}, loadoutOps: [] }
}

function load(): State {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as State) : {}
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
  return state[slot] ?? emptySlot()
}

function updateSlot(slot: number, fn: (s: SlotPending) => SlotPending) {
  const next = { ...state, [slot]: fn(getSlot(state, slot)) }
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

export function toggleSell(slot: number, gaHandle: number) {
  updateSlot(slot, (s) => {
    const has = s.sells.includes(gaHandle)
    return {
      ...s,
      sells: has
        ? s.sells.filter((h) => h !== gaHandle)
        : [...s.sells, gaHandle],
    }
  })
}

export function setFavorite(
  slot: number,
  gaHandle: number,
  desired: boolean | null,
) {
  updateSlot(slot, (s) => {
    const favorites = { ...s.favorites }
    if (desired === null) delete favorites[gaHandle]
    else favorites[gaHandle] = desired
    return { ...s, favorites }
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

/** Clear only the relic edits (sells + bookmarks) for a slot, keeping loadout ops. */
export function clearSlotRelics(slot: number) {
  updateSlot(slot, (s) => ({ ...s, sells: [], favorites: {} }))
}

export function clearAll() {
  setState({})
}

// --- selectors / hooks -----------------------------------------------------

export function slotCount(s: SlotPending): number {
  return s.sells.length + Object.keys(s.favorites).length + s.loadoutOps.length
}

/** Reactive snapshot of one slot's pending changes. */
export function usePendingSlot(slot: number | null | undefined): SlotPending {
  const snap = useSyncExternalStore(subscribe, () => state)
  return slot == null ? emptySlot() : getSlot(snap, slot)
}

/** Reactive total count across all slots (for the navbar badge). */
export function usePendingTotal(): { count: number; slots: number[] } {
  const snap = useSyncExternalStore(subscribe, () => state)
  const slots = Object.keys(snap).map(Number)
  const count = slots.reduce((sum, k) => sum + slotCount(snap[k]), 0)
  return { count, slots }
}

/** Non-reactive read of a slot (for export handlers). */
export function readSlot(slot: number): SlotPending {
  return getSlot(state, slot)
}

export function readAll(): State {
  return state
}
