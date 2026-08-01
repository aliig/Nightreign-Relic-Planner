/**
 * Working diff of edits the user has made to their save, shared across pages.
 *
 * The app treats these edits as already applied to a live, in-memory copy of the
 * save: trashed relics drop out of the inventory, deleted loadouts disappear, new
 * ones show up inline, and Relic Rites purchases ("mints") appear as Incoming
 * relics. This store IS that diff (and the change log) — nothing is written to
 * disk until the user exports. Every edit (sell/bookmark a relic, buy relics,
 * add/delete/rename/overwrite a loadout, reset vessels/loadouts) lives here.
 *
 * The optimizer consumes the diff too: stagedFields() puts it on optimize /
 * snapshot requests (server-side effective inventory) and stagedKey() folds it
 * into query/cache keys so results stale + re-run when the diff changes.
 *
 * Keyed by save-slot index (the selected profile). Backed by localStorage so the
 * diff survives SPA navigation, tab close, and browser restart — only a new save
 * upload (or explicit discard) resets it, and the upload divergence gate warns
 * first when the new save doesn't already contain these edits. It is never sent
 * to a server as state, so it does NOT follow the account to another device.
 * The save File itself is held separately in saveFile.ts (in-memory) with a
 * durable copy in saveBackup.ts (IndexedDB).
 */
import { useEffect, useRef, useSyncExternalStore } from "react"

import { effectCountOf, sellValue } from "./sellValue"

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
export type RelicMeta = {
  name: string
  isDeep?: boolean
  murk?: number
  /** How many builds use this relic — surfaced as a sell-impact warning. */
  builds?: number
  /**
   * Content fingerprint [real_id, e1, e2, e3, c1, c2, c3] captured at sell
   * time. The game renumbers ga_handles on every save, so this is the only
   * identity that survives a re-upload — the upload divergence gate uses it
   * to detect whether a staged sell was actually applied in-game.
   */
  fp?: number[]
}

/**
 * A relic to MINT (add) into the save — from a Relic Rites purchase. Self-contained:
 * carries the full spec so it never depends on a ga_handle or index (those don't exist
 * until the game assigns them at add time). Never re-rolled; the concrete relic is fixed
 * at "purchase" time.
 */
export type MintSpec = {
  id: string
  /**
   * Client-assigned SYNTHETIC ga_handle, always negative (real handles are
   * large positive u32s carrying the relic type nibble, so the ranges cannot
   * collide). Stable for the life of the mint: optimizer requests/results,
   * staged loadout ops, and the export-time substitution with the real handle
   * (X-Added-Handles) all reference the mint by this value.
   */
  handle: number
  real_id: number
  item_id: number
  effects: number[] // 3, EMPTY (0xFFFFFFFF) for empty slots
  curses: number[] // 3
  name: string
  color: string
  tier: string
  isDeep: boolean
  /** "exact" | "approximate" | "targeted" — provenance of the color/tier odds. */
  oddsSource: string
  /** Build names whose top-N loadouts use this relic (why it's a keeper). */
  builds?: string[]
}

export type SlotPending = {
  sells: number[] // ga_handles to sell
  favorites: Record<number, boolean> // ga_handle -> desired bookmark state
  loadoutOps: PendingLoadoutOp[]
  // Relics to mint (Relic Rites purchases). Applied on export via export-add-relics.
  mints: MintSpec[]
  // Net Murk delta from purchases (negative = cost, after dud sell-refunds). Applied
  // on export alongside the mints. Tied to mints — reset to 0 when no mints remain.
  murkDelta: number
  // Label cache keyed by ga_handle (relic name / murk value) for the change log.
  // Purely cosmetic; pruned to the handles still referenced by sells/favorites.
  meta: Record<number, RelicMeta>
  // Identity (profile.id) of the save these edits were computed against. Profile
  // rows are recreated on every re-upload, so a changed id means the underlying
  // save was replaced (here or on another device) and the diff is stale.
  baseId?: string
}

type State = Record<number, SlotPending>

const STORAGE_KEY = "pendingChanges"

function emptySlot(): SlotPending {
  return {
    sells: [],
    favorites: {},
    loadoutOps: [],
    mints: [],
    murkDelta: 0,
    meta: {},
  }
}

function load(): State {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as State
    // Normalize so every slot has the full shape (sessions saved before `meta`
    // was added would otherwise read back without it and crash raw-state readers).
    const out: State = {}
    for (const [k, v] of Object.entries(parsed)) {
      out[Number(k)] = { ...emptySlot(), ...v }
    }
    return backfillMintHandles(out)
  } catch {
    return {}
  }
}

/** Assign synthetic handles to mints staged before the `handle` field existed. */
function backfillMintHandles(out: State): State {
  let min = 0
  for (const s of Object.values(out)) {
    for (const m of s.mints) {
      if (typeof m.handle === "number" && m.handle < min) min = m.handle
    }
  }
  for (const s of Object.values(out)) {
    for (const m of s.mints) {
      if (typeof m.handle !== "number" || m.handle >= 0) {
        min -= 1
        m.handle = min
      }
    }
  }
  return out
}

/** Next unused synthetic mint handle (negative, unique across ALL slots). */
function nextMintHandle(): number {
  let min = 0
  for (const s of Object.values(state)) {
    for (const m of s.mints) {
      if (m.handle < min) min = m.handle
    }
  }
  return min - 1
}

let state: State = load()
const listeners = new Set<() => void>()

// localStorage is shared across tabs, but each tab keeps its own in-memory
// `state`. Without this, a write in tab B would be invisible to tab A, and tab A's
// next persist would clobber it. The `storage` event fires only in *other* tabs,
// so reload the diff and re-render when another tab changes it. (sessionStorage was
// per-tab and never needed this.)
if (typeof window !== "undefined") {
  window.addEventListener("storage", (e) => {
    if (e.key !== STORAGE_KEY) return
    state = load()
    for (const l of listeners) l()
  })
}

// The profile identity each slot's diff was last stamped against, captured when
// the inventory/loadouts for that slot loads. Not persisted — it's rebuilt from
// the server's current profiles each session and only used to stamp new edits.
const currentBaseBySlot = new Map<number, string>()

function persist() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
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
  const base = getSlot(state, slot)
  const updated = pruneMeta(fn(base))
  // Stamp the profile identity this edit was made against (keep an existing
  // stamp; otherwise take the slot's current one) so a later re-upload can be
  // detected as stale. See noteSlotBase.
  updated.baseId = base.baseId ?? currentBaseBySlot.get(slot)
  const next = { ...state, [slot]: updated }
  // Drop the slot entry entirely if it became empty (keeps counts clean).
  const s = next[slot]
  if (
    s.sells.length === 0 &&
    Object.keys(s.favorites).length === 0 &&
    s.loadoutOps.length === 0 &&
    s.mints.length === 0
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

/**
 * Stage relics to mint (a Relic Rites purchase batch) and the batch's net Murk delta
 * (negative = cost after dud sell-refunds). Each spec gets a stable id.
 */
export function addMints(
  slot: number,
  specs: Array<Omit<MintSpec, "id" | "handle">>,
  murkDelta: number,
): void {
  if (!specs.length && murkDelta === 0) return
  const base = nextMintHandle()
  updateSlot(slot, (s) => ({
    ...s,
    mints: [
      ...s.mints,
      ...specs.map((spec, i) => ({ ...spec, id: nextId(), handle: base - i })),
    ],
    murkDelta: s.murkDelta + murkDelta,
  }))
}

/**
 * Un-stage one minted relic. Faithful buy-then-sell: the relic was still bought
 * in the simulated session, so removing it credits its SELL value back into the
 * batch's Murk delta (exactly like deselecting it before staging). Removing the
 * LAST mint instead cancels the whole purchase session — no mints staged means
 * the session never happened, so the delta resets to 0.
 */
export function removeMint(slot: number, id: string): void {
  updateSlot(slot, (s) => {
    const removed = s.mints.find((m) => m.id === id)
    if (!removed) return s
    const mints = s.mints.filter((m) => m.id !== id)
    // Cascade: staged loadout ops referencing the mint's synthetic handle can
    // never export (the relic will not exist), so they go with it. Callers
    // confirm with the user first when mintReferences() is non-empty.
    const loadoutOps = s.loadoutOps.filter(
      (o) => !("ga_handles" in o && o.ga_handles.includes(removed.handle)),
    )
    return {
      ...s,
      mints,
      loadoutOps,
      murkDelta:
        mints.length === 0
          ? 0
          : s.murkDelta +
            sellValue(effectCountOf(removed.effects), removed.isDeep),
    }
  })
}

/** Staged loadout ops that place a given mint (by its synthetic handle). */
export function mintReferences(
  s: SlotPending,
  handle: number,
): PendingLoadoutOp[] {
  return s.loadoutOps.filter(
    (o) => "ga_handles" in o && o.ga_handles.includes(handle),
  )
}

export function clearSlot(slot: number) {
  const next = { ...state }
  delete next[slot]
  setState(next)
}

export function clearAll() {
  setState({})
}

/**
 * Record the profile identity currently loaded for a slot and reconcile any
 * existing diff against it. Returns true iff a stale diff was cleared.
 *
 * - No diff for the slot: just remember the id (future edits stamp with it).
 * - Diff with no stamp (legacy/pre-feature): adopt the current id as its base.
 * - Diff stamped against a different id: the save was re-uploaded (here or on
 *   another device) since the edits were made, so index-based loadout ops would
 *   mis-fire against a changed save — discard the whole slot.
 */
export function noteSlotBase(slot: number, currentId: string): boolean {
  currentBaseBySlot.set(slot, currentId)
  const s = state[slot]
  if (!s) return false
  if (s.baseId == null) {
    setState({ ...state, [slot]: { ...s, baseId: currentId } })
    return false
  }
  if (s.baseId !== currentId) {
    clearSlot(slot)
    return true
  }
  return false
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

/**
 * Reconcile pending diffs against the currently-loaded profiles. Re-runs only
 * when the set of (slot, profile id) pairs changes. Calls onStale with the slot
 * indexes whose edits were discarded because their save was re-uploaded.
 */
export function useReconcileSlotBases(
  bases: Array<{ slot: number; id: string }>,
  onStale?: (slots: number[]) => void,
): void {
  // `bases`/`onStale` are fresh every render, so the effect runs each render; the
  // lastKey guard makes it a cheap no-op until the (slot, profile id) set actually
  // changes — i.e. real work happens once per save (re)load, not per render.
  const lastKey = useRef<string | null>(null)
  useEffect(() => {
    const key = bases.map((b) => `${b.slot}:${b.id}`).join(",")
    if (key === lastKey.current) return
    lastKey.current = key
    const cleared: number[] = []
    for (const b of bases) {
      if (noteSlotBase(b.slot, b.id)) cleared.push(b.slot)
    }
    if (cleared.length) onStale?.(cleared)
  }, [bases, onStale])
}

/** Non-reactive read of a slot (for export handlers). */
export function readSlot(slot: number): SlotPending {
  return getSlot(state, slot)
}

export function readAll(): State {
  return state
}

// --- staged-diff views (optimizer requests + cache keys) --------------------

/** The slot's staged diff in the backend's wire shape (OptimizeRequest /
 * SnapshotQuery `staged_sells` + `staged_mints`). Empty arrays when clean. */
export function stagedFields(s: SlotPending): {
  staged_sells: number[]
  staged_mints: Array<{
    handle: number
    real_id: number
    effects: number[]
    curses: number[]
  }>
} {
  return {
    staged_sells: [...s.sells],
    staged_mints: s.mints.map((m) => ({
      handle: m.handle,
      real_id: m.real_id,
      effects: m.effects,
      curses: m.curses,
    })),
  }
}

/**
 * Cheap stable identity of a slot's staged diff, for query/cache keys — `""`
 * when clean. Mint handles are stable and 1:1 with their content, so they
 * identify the mint set without hashing effects.
 */
export function stagedKey(s: SlotPending): string {
  if (s.sells.length === 0 && s.mints.length === 0) return ""
  const sells = [...s.sells].sort((a, b) => a - b).join(",")
  const mints = s.mints
    .map((m) => m.handle)
    .sort((a, b) => a - b)
    .join(",")
  return `s:${sells}|m:${mints}`
}

/** Per-slot rollup of the staged diff (upload divergence gate dialog). */
export type SlotSummary = {
  slot: number
  sells: number
  /** Total Murk the staged sells would refund (from cached labels). */
  sellRefund: number
  mints: number
  murkDelta: number
  favorites: number
  loadoutOps: number
}

export function summarizePending(all: State): SlotSummary[] {
  return Object.entries(all)
    .map(([slot, s]) => ({
      slot: Number(slot),
      sells: s.sells.length,
      sellRefund: s.sells.reduce((sum, h) => sum + (s.meta[h]?.murk ?? 0), 0),
      mints: s.mints.length,
      murkDelta: s.murkDelta,
      favorites: Object.keys(s.favorites).length,
      loadoutOps: s.loadoutOps.length,
    }))
    .sort((a, b) => a.slot - b.slot)
}
