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
  // Net Murk delta of the committed Relic Rites batch (negative = cost, after dud
  // sell-refunds). Applied on export alongside the mints. Stands on its own even
  // with zero mints: rolling IS buying, so an all-dud batch still costs its
  // buy/sell spread (1:1 with the in-game walk).
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
    s.mints.length === 0 &&
    s.murkDelta === 0
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

/** A mint for the staged rites batch. `id`/`handle` are optional: pass a
 * surviving mint's values to preserve its identity across a batch refresh
 * (staged loadout ops reference mints by synthetic handle). */
export type RitesBatchSpec = Omit<MintSpec, "id" | "handle"> &
  Partial<Pick<MintSpec, "id" | "handle">>

/**
 * Commit (or refresh) THE staged Relic Rites batch for a slot — there is
 * exactly one. The roll stream is deterministic per save state, so a re-run of
 * Find Keepers re-views the same stream: the batch is REPLACED, never stacked
 * (stacking would double-buy the same rolls). `murkDelta` is the batch's
 * absolute net Murk (negative = cost after dud refunds) and is committed even
 * with zero kept relics: rolling IS buying, so an all-dud batch still costs
 * its buy/sell spread — that loss must export, or previewing a bad roll would
 * be free (anti-save-scum, 1:1 fidelity).
 *
 * Staged loadout ops referencing a mint that did not survive the refresh are
 * dropped (they could never export); their labels are returned so callers can
 * warn the user.
 */
export function replaceRitesBatch(
  slot: number,
  specs: RitesBatchSpec[],
  murkDelta: number,
): string[] {
  const dropped: string[] = []
  // Fresh handles go below the global minimum, which already accounts for the
  // preserved handles (they exist in the current state being replaced).
  let nextHandle = nextMintHandle()
  updateSlot(slot, (s) => {
    const mints = specs.map((spec) => ({
      ...spec,
      id: spec.id ?? nextId(),
      handle: spec.handle ?? nextHandle--,
    }))
    const surviving = new Set(mints.map((m) => m.handle))
    const loadoutOps = s.loadoutOps.filter((o) => {
      if (!("ga_handles" in o)) return true
      // Synthetic (negative) handles are mint refs; real handles always survive.
      const ok = o.ga_handles.every((h) => h >= 0 || surviving.has(h))
      if (!ok)
        dropped.push(
          o.kind === "add"
            ? `Add loadout "${o.name}"`
            : `Replace "${o.targetName || "loadout"}"`,
        )
      return ok
    })
    return { ...s, mints, loadoutOps, murkDelta }
  })
  return dropped
}

/**
 * Cancel the slot's staged rites batch outright: mints, the batch Murk delta,
 * and any staged loadout ops referencing the batch's synthetic handles. This
 * is the deliberate escape hatch (misclicks, changed mind before export) — the
 * default path keeps the rolled batch committed. Returns the labels of any
 * dropped loadout ops so callers can warn.
 */
export function clearRitesBatch(slot: number): string[] {
  const dropped: string[] = []
  updateSlot(slot, (s) => ({
    ...s,
    mints: [],
    murkDelta: 0,
    loadoutOps: s.loadoutOps.filter((o) => {
      if (!("ga_handles" in o && o.ga_handles.some((h) => h < 0))) return true
      dropped.push(
        o.kind === "add"
          ? `Add loadout "${o.name}"`
          : `Replace "${o.targetName || "loadout"}"`,
      )
      return false
    }),
  }))
  return dropped
}

/**
 * Un-stage one minted relic. Faithful buy-then-sell: the relic was still
 * bought in the batch, so removing it credits its SELL value back into the
 * batch's Murk delta (exactly like unchecking it on the Rites page). The
 * batch itself stands — removing every mint leaves the all-sold spread loss,
 * not a clean slate (use clearRitesBatch to cancel the batch).
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
        s.murkDelta + sellValue(effectCountOf(removed.effects), removed.isDeep),
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

/**
 * Net Murk adjustment of a slot's staged diff: the mint batches' net delta
 * (purchases minus dud refunds, usually negative) plus every staged sell's
 * cached refund. This is exactly what export applies (sells credit first,
 * then the mint delta), so `save Murk + murkAdjustment` is the Murk the
 * exported save will hold.
 */
export function murkAdjustment(s: SlotPending): number {
  const sellRefund = s.sells.reduce((n, h) => n + (s.meta[h]?.murk ?? 0), 0)
  return s.murkDelta + sellRefund
}

/**
 * Live "effective" Murk for a slot: the save's Murk with the staged diff
 * applied, clamped to the save field's range [0, u32]. Every surface that
 * shows or spends Murk should use this — staged actions persist everywhere
 * until export. Null when the save value is unknown.
 */
export function effectiveMurks(
  saveMurks: number | null | undefined,
  s: SlotPending,
): number | null {
  if (saveMurks == null) return null
  return Math.max(0, Math.min(saveMurks + murkAdjustment(s), 0xffffffff))
}

// --- effective-state composition (the sanctioned way to read save state) ----
//
// Every surface that shows or computes save-derived state must compose
// (base state + this slot's staged diff) through these helpers — reading the
// base raw is how staged edits "disappear" on some page. If a page needs a
// shape these don't cover, add a selector here rather than hand-rolling.

/** A staged mint as a snake_case relic row (the shape save-derived relic
 * lists use), marked `incoming`. */
export type StagedRelicRow = {
  id: string
  ga_handle: number
  real_id: number
  name: string
  color: string
  tier: string
  is_deep: boolean
  effect_1: number
  effect_2: number
  effect_3: number
  curse_1: number
  curse_2: number
  curse_3: number
  incoming: true
}

/** The slot's staged mints as relic rows, for appending to a base list. */
export function stagedMintRows(s: SlotPending): StagedRelicRow[] {
  return s.mints.map((m) => ({
    id: m.id,
    ga_handle: m.handle,
    real_id: m.real_id,
    name: m.name,
    color: m.color,
    tier: m.tier,
    is_deep: m.isDeep,
    effect_1: m.effects[0],
    effect_2: m.effects[1],
    effect_3: m.effects[2],
    curse_1: m.curses[0],
    curse_2: m.curses[1],
    curse_3: m.curses[2],
    incoming: true,
  }))
}

/** Base relic rows minus the slot's staged sells (trashed -> already gone). */
export function excludeStagedSells<T extends { ga_handle: number }>(
  rows: T[],
  s: SlotPending,
): T[] {
  if (s.sells.length === 0) return rows
  const sold = new Set(s.sells)
  return rows.filter((r) => !sold.has(r.ga_handle))
}

/** Full effective relic-row list: base − staged sells + staged mints. */
export function effectiveRelicRows<T extends { ga_handle: number }>(
  rows: T[],
  s: SlotPending,
): Array<T | StagedRelicRow> {
  return [...excludeStagedSells(rows, s), ...stagedMintRows(s)]
}

/** Resolve a synthetic (negative) mint handle across ALL slots — pins and
 * loadout refs are stored per build/save, not per page. */
export function stagedMintByHandle(handle: number): MintSpec | undefined {
  for (const s of Object.values(state)) {
    const m = s.mints.find((mm) => mm.handle === handle)
    if (m) return m
  }
  return undefined
}

/** The slot's pending loadout ops bucketed by kind/index — the one shape
 * every preset-list surface composes with (Loadouts page, save-as-loadout
 * target picker). */
export type LoadoutOpBuckets = {
  renameByIndex: Map<number, { id: string; name: string }>
  deleteByIndex: Map<number, string>
  overwriteByIndex: Map<
    number,
    { id: string; ga_handles: number[]; name?: string }
  >
  adds: Array<{
    id: string
    name: string
    character: string
    vesselName?: string
    ga_handles: number[]
  }>
  resetVesselsId?: string
  resetPresetsId?: string
}

export function bucketLoadoutOps(s: SlotPending): LoadoutOpBuckets {
  const out: LoadoutOpBuckets = {
    renameByIndex: new Map(),
    deleteByIndex: new Map(),
    overwriteByIndex: new Map(),
    adds: [],
  }
  for (const op of s.loadoutOps) {
    if (op.kind === "rename")
      out.renameByIndex.set(op.index, { id: op.id, name: op.name })
    else if (op.kind === "delete") out.deleteByIndex.set(op.index, op.id)
    else if (op.kind === "overwrite")
      out.overwriteByIndex.set(op.index, {
        id: op.id,
        ga_handles: op.ga_handles,
        name: op.name,
      })
    else if (op.kind === "add")
      out.adds.push({
        id: op.id,
        name: op.name,
        character: op.character,
        vesselName: op.vesselName,
        ga_handles: op.ga_handles,
      })
    else if (op.kind === "reset_vessels") out.resetVesselsId = op.id
    else if (op.kind === "reset_presets") out.resetPresetsId = op.id
  }
  return out
}

/** A valid "replace this loadout" target under the LIVE preset list. */
export type ReplaceTarget =
  | {
      kind: "existing"
      index: number
      name: string
      /** An earlier staged overwrite of the same preset (superseded on queue). */
      staleOverwriteId?: string
    }
  | { kind: "staged-add"; opId: string; name: string }

/**
 * The loadouts a "save as loadout → replace" flow may target, composed with
 * the staged diff: staged deletes are not targets (an overwrite would
 * silently fight the delete), staged renames show their new names, a staged
 * full reset leaves only staged adds, and staged adds ARE targets (replacing
 * one swaps the add op in place — it has no in-save index yet).
 */
export function replaceTargets(
  existing: Array<{ index: number; name: string }>,
  s: SlotPending,
  character: string,
): ReplaceTarget[] {
  const b = bucketLoadoutOps(s)
  const out: ReplaceTarget[] = []
  if (b.resetPresetsId === undefined) {
    for (const e of existing) {
      if (b.deleteByIndex.has(e.index)) continue
      out.push({
        kind: "existing",
        index: e.index,
        name: b.renameByIndex.get(e.index)?.name ?? e.name,
        staleOverwriteId: b.overwriteByIndex.get(e.index)?.id,
      })
    }
  }
  for (const a of b.adds) {
    if (a.character === character)
      out.push({ kind: "staged-add", opId: a.id, name: a.name })
  }
  return out
}

/**
 * Queue "replace `target` with this relic set" with clean diff semantics:
 * an existing preset gets ONE overwrite op (a repeat replace supersedes the
 * earlier staged op), and a staged add is swapped in place (same name, new
 * vessel/relics) — never an overwrite, it has no in-save index yet.
 */
export function queueReplaceLoadout(
  slot: number,
  target: ReplaceTarget,
  spec: {
    character: string
    vessel_id: number
    ga_handles: number[]
    vesselName?: string
  },
): void {
  if (target.kind === "staged-add") {
    removeLoadoutOp(slot, target.opId)
    addLoadoutOp(slot, {
      kind: "add",
      character: spec.character,
      vessel_id: spec.vessel_id,
      ga_handles: spec.ga_handles,
      name: target.name,
      vesselName: spec.vesselName,
    })
    return
  }
  if (target.staleOverwriteId) removeLoadoutOp(slot, target.staleOverwriteId)
  addLoadoutOp(slot, {
    kind: "overwrite",
    index: target.index,
    character: spec.character,
    vessel_id: spec.vessel_id,
    ga_handles: spec.ga_handles,
    targetName: target.name,
  })
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
