import {
  AlertTriangle,
  BookMarked,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Loader2,
  Pin,
  RotateCcw,
  Trophy,
  X,
  XCircle,
  Zap,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"

import {
  ApiError,
  type BuildChange,
  OptimizeService,
  type SlotAlternativeRequest,
  type VesselResult,
} from "@/client"
import { ChangeRelicGroups } from "@/components/ChangeRelics"
import { COLOR_HEX, RelicNameCell } from "@/components/RelicDisplay"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import useCustomToast from "@/hooks/useCustomToast"
import {
  describeBuildChange,
  isChangeNews,
  rawScoreTooltip,
} from "@/lib/buildChange"
import {
  addLoadoutOp,
  findLoadoutNameConflict,
  queueReplaceLoadout,
  type ReplaceTarget,
  replaceTargets,
  usePendingSlot,
} from "@/lib/pendingChanges"
import {
  findSavedLoadoutMatch,
  type RelicContent,
  relicContentKey as relicKey,
} from "@/lib/savedLoadoutMatch"

export { relicKey }

// --- Types ---

export type SlotAssignment = VesselResult["assignments"][number]

// --- Helpers ---

/** Suffix explaining why a listed effect scored nothing, if it did. */
export function breakdownNote(b: Record<string, unknown>): string {
  if (!b.redundant) return ""
  switch (b.override_status) {
    case "over_limit_penalty":
      return " (over limit)"
    // The Nightfarer can't use it — the game greys it out, so it never fires.
    case "character_incompatible":
      return " (no effect)"
    default:
      return " (redundant)"
  }
}

export function getBreakdownColor(
  b: Record<string, unknown>,
): string | undefined {
  const category = b.category as string | null
  if (!category || category === "excluded") return undefined
  if (category === "required") return "#FF8C00"
  const weight = (b.weight as number) ?? 0
  if (weight >= 75) return "#FF4444"
  if (weight >= 35) return "#4488FF"
  if (weight >= 15) return "#44BB88"
  if (weight >= 1) return "#9966CC"
  return "#888888"
}

// --- Stacked curses ---

/** Override statuses where the game itself neutralizes an effect, so a second
 *  copy costs the player nothing: greyed out for this Nightfarer, or beaten by
 *  a desired effect in the same exclusive category. Every other status is a
 *  scoring-only note (a user limit, a penalty) — the curse still fires in-game. */
const INERT_CURSE_STATUSES = new Set([
  "character_incompatible",
  "excl_category_nullified",
])

export type StackedCurse = { name: string; count: number; slots: number[] }

/** Curses carried by more than one relic in the same vessel.
 *
 *  Curses stack: every one of the 24 debuff effects in the game data resolves
 *  to stacking type "stack" (SourceDataHandler.get_effect_stacking_type over
 *  resources/json/stacking_rules.json), so a second "Reduced Rune Acquisition"
 *  is a second full penalty, not a no-op.
 *
 *  A curse the build weights POSITIVELY is skipped — some builds want their
 *  curses, and the optimizer picked those copies on purpose. */
export function stackedCurses(assignments: SlotAssignment[]): StackedCurse[] {
  const byName = new Map<string, { count: number; slots: Set<number> }>()
  const wanted = new Set<string>()
  for (const slot of assignments) {
    for (const b of slot.breakdown ?? []) {
      if (!b.is_curse) continue
      const name = b.name as string
      if (!name) continue
      if (((b.weight as number) ?? 0) > 0) {
        wanted.add(name)
        continue
      }
      if (INERT_CURSE_STATUSES.has(b.override_status as string)) continue
      const entry = byName.get(name) ?? { count: 0, slots: new Set<number>() }
      entry.count += 1
      entry.slots.add(slot.slot_index)
      byName.set(name, entry)
    }
  }
  return [...byName.entries()]
    .filter(([name, e]) => e.count > 1 && !wanted.has(name))
    .map(([name, e]) => ({
      name,
      count: e.count,
      slots: [...e.slots].sort((a, b) => a - b),
    }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name))
}

// --- Result cache (persists across route navigations, clears on page reload) ---

/** One base key (build + profile + upload) holds the latest results plus the
 *  staged-diff signature they were computed with. A sig mismatch means the
 *  results are STALE for the current staged state — still shown (with a
 *  banner) while the auto re-run streams the staged replacement. */
export type OptimizeCacheEntry = { sig: string; results: VesselResult[] }

export const resultCache = new Map<string, OptimizeCacheEntry>()

export function cacheKey(
  ...parts: (string | number | null | undefined)[]
): string {
  return parts.map((p) => String(p ?? "")).join(":")
}

// --- Change highlighting (save-diff) ---

/** Content keys of relics that entered the best arrangement (for "NEW" badges).
 *  Only meaningful when the change is news — a build edit re-baselines and is
 *  not a "since last save" event, so nothing is tagged NEW for it. A relic
 *  bought in Relic Rites IS news: the user owns it, and it just earned a slot. */
export function enteredKeys(change?: BuildChange | null): Set<string> {
  const keys = new Set<string>()
  if (!change || !isChangeNews(change)) return keys
  for (const r of change.entered ?? []) keys.add(relicKey(r))
  return keys
}

// --- SSE streaming ---

// Moved to lib/optimizeStream.ts so the background bulk job can drive the same
// reader without importing this component module; re-exported here because the
// optimize page imports both it and the results table from this file.
export { type OptimizeProgress, runOptimizeStream } from "@/lib/optimizeStream"

// --- Single-slot re-optimization ("strike a relic") ---

/** How the optimizer should source the relic inventory for a re-optimize:
 *  DB mode (authenticated build + profile, optionally with the staged in-app
 *  diff) or inline mode (anonymous — the diff is already applied to `relics`
 *  client-side). */
export type InventorySource =
  | {
      build_id: string
      profile_id: string
      staged_sells?: number[]
      staged_mints?: Array<{
        handle: number
        real_id: number
        effects: number[]
        curses: number[]
      }>
    }
  | { build: Record<string, unknown>; relics: unknown[] }

/** Re-optimize a single vessel slot, keeping every other slot frozen in its
 *  exact position and excluding the struck relic(s). Returns the updated vessel,
 *  or null when no arrangement exists at all (the more common "no replacement"
 *  case returns a vessel whose struck slot relic is null). */
export async function fetchSlotAlternative(params: {
  inventorySource: InventorySource
  vessel_id: number
  struck_slot_index: number
  locked_slots: Array<{ slot_index: number; ga_handle: number }>
  excluded_ga_handles: number[]
}): Promise<VesselResult | null> {
  const { inventorySource, ...strike } = params
  try {
    const result = await OptimizeService.optimizeSlotAlternative({
      requestBody: { ...inventorySource, ...strike } as SlotAlternativeRequest,
    })
    return (result ?? null) as VesselResult | null
  } catch (err) {
    if (err instanceof ApiError) {
      const detail = (err.body as { detail?: string } | null)?.detail
      throw new Error(detail ?? "Failed to find an alternative")
    }
    throw err
  }
}

// --- Components ---

export function SlotCard({
  slot,
  isPinned = false,
  isNew = false,
  onStrike,
  isStriking = false,
  busy = false,
  noAlternative = false,
  stackedCurseCounts,
}: {
  slot: SlotAssignment
  isPinned?: boolean
  isNew?: boolean
  /** Curse name -> how many relics in this whole vessel carry it (>1 only).
   *  Marks the rows behind the vessel's stacked-curse warning. */
  stackedCurseCounts?: Map<string, number>
  /** When provided, renders an X to reject this relic and re-optimize the slot. */
  onStrike?: () => void
  /** This slot's re-optimization is in flight (shows a spinner). */
  isStriking?: boolean
  /** Any slot in this vessel is re-optimizing (disables striking everywhere). */
  busy?: boolean
  /** No replacement relic fits this slot — keep the relic, disable striking. */
  noAlternative?: boolean
}) {
  const relic = slot.relic
  const effects =
    slot.breakdown?.filter((b: Record<string, unknown>) => !b.is_curse) ?? []
  const curses =
    slot.breakdown?.filter((b: Record<string, unknown>) => b.is_curse) ?? []

  return (
    <div
      className={`rounded-md border p-3 space-y-1.5${
        isNew
          ? " border-green-500/50 bg-green-500/5"
          : isPinned
            ? " border-primary/40 bg-primary/5"
            : ""
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span
            className="w-2.5 h-2.5 rounded-full shrink-0"
            style={{ background: COLOR_HEX[slot.slot_color] ?? "#888" }}
            title={slot.slot_color}
          />
          <span className="text-xs text-muted-foreground">
            Slot {slot.slot_index + 1} {slot.is_deep ? "(Deep)" : ""}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          {isNew && (
            <Badge className="h-4 px-1.5 py-0 text-[10px] bg-green-600 text-white hover:bg-green-600">
              NEW
            </Badge>
          )}
          {relic != null && ((relic as any).ga_handle as number) < 0 && (
            <Badge
              className="h-4 px-1.5 py-0 text-[10px] bg-sky-600 text-white hover:bg-sky-600"
              title="Staged Relic Rites purchase — not in your save until you export"
            >
              Incoming
            </Badge>
          )}
          {isPinned && (
            <span title="Pinned relic">
              <Pin className="h-3 w-3 text-primary shrink-0" />
            </span>
          )}
          <span className="text-xs font-mono font-semibold">
            {slot.score} pts
          </span>
          {onStrike && relic && (
            <button
              type="button"
              onClick={onStrike}
              disabled={busy || noAlternative}
              aria-label={`Reject ${relic.name} and find the next-best for this slot`}
              title="Reject this relic; find the next-best for this slot"
              className="hover:opacity-70 disabled:opacity-40 shrink-0"
            >
              {isStriking ? (
                <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
              ) : (
                <X className="h-3 w-3 text-muted-foreground" />
              )}
            </button>
          )}
        </div>
      </div>
      {noAlternative && (
        <p className="text-[10px] italic text-muted-foreground">
          No other relic fits this slot
        </p>
      )}
      {relic ? (
        <>
          <RelicNameCell
            name={relic.name}
            color={relic.color}
            tier={relic.tier}
            isDeep={relic.is_deep}
          />
          {effects.length > 0 && (
            <div className="space-y-0.5 mt-1">
              {effects.map((b: Record<string, unknown>, i: number) => (
                <div
                  key={i}
                  className="flex items-center justify-between text-xs"
                >
                  <span
                    className="truncate"
                    style={{ color: getBreakdownColor(b) }}
                  >
                    {b.name as string}
                    {breakdownNote(b)}
                  </span>
                  <span className="font-mono ml-2 shrink-0">
                    {(b.score as number) >= 0 ? "+" : ""}
                    {b.score as number}
                  </span>
                </div>
              ))}
            </div>
          )}
          {curses.length > 0 && (
            <div className="mt-1.5 pt-1.5 border-t border-destructive/20">
              <div className="space-y-0.5">
                {curses.map((b: Record<string, unknown>, i: number) => (
                  <div
                    key={i}
                    className="flex items-center justify-between text-xs"
                  >
                    <span className="truncate text-destructive/80">
                      {b.name as string}
                      {breakdownNote(b)}
                      {stackedCurseCounts?.has(b.name as string) && (
                        <span
                          className="ml-1 font-semibold text-destructive"
                          title={`${stackedCurseCounts.get(b.name as string)} copies of this curse in this loadout — curses stack, so the penalty applies ${stackedCurseCounts.get(b.name as string)} times`}
                        >
                          ×{stackedCurseCounts.get(b.name as string)}
                        </span>
                      )}
                    </span>
                    <span className="font-mono ml-2 shrink-0 text-destructive/80">
                      {(b.score as number) >= 0 ? "+" : ""}
                      {b.score as number}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      ) : (
        <p className="text-xs text-muted-foreground italic">
          No relic assigned
        </p>
      )}
    </div>
  )
}

/**
 * Marks a cumulative total that only applies in a context (e.g. "when HP below
 * 40%") so it never reads as an always-on bonus.
 */
function ConditionalBadge({ text }: { text: string }) {
  return (
    <span className="ml-1 rounded bg-amber-500/15 px-1 py-px text-[10px] font-medium text-amber-600 dark:text-amber-400 shrink-0 whitespace-nowrap">
      {text}
    </span>
  )
}

/**
 * Cumulative stacked-effect summary shown under each vessel: the single biggest
 * bonus at a glance, plus a "see all" toggle listing every family's cumulative %.
 * Rendered outside the (clickable) CardHeader so it stays visible when collapsed
 * and its toggle never triggers the card's expand/collapse.
 */
export function CumulativeSummary({
  groups,
  className = "px-6 pb-3 text-xs",
}: {
  groups: VesselResult["cumulative_effects"]
  className?: string
}) {
  const [open, setOpen] = useState(false)
  if (!groups || groups.length === 0) return null
  const top = groups.find((g) => g.is_top) ?? groups[0]
  return (
    <div className={className}>
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 min-w-0">
          <Zap className="h-3.5 w-3.5 text-amber-500 shrink-0" />
          <span className="font-medium truncate">{top.family}</span>
          <span className="font-mono text-muted-foreground shrink-0">
            {top.bonus_display}
          </span>
          {top.conditional && <ConditionalBadge text={top.conditional} />}
        </span>
        {groups.length > 1 && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="text-muted-foreground hover:text-foreground shrink-0 flex items-center gap-0.5"
          >
            {open ? "See less" : `See all (${groups.length})`}
            {open ? (
              <ChevronUp className="h-3 w-3" />
            ) : (
              <ChevronDown className="h-3 w-3" />
            )}
          </button>
        )}
      </div>
      {open && (
        <div className="mt-2 space-y-1 border-t pt-2">
          {groups.map((g) => (
            <div
              key={g.family}
              className="flex items-center justify-between gap-2"
            >
              <span className="text-muted-foreground truncate">
                <span className="text-foreground">{g.family}</span>{" "}
                {g.tiers
                  .map((t) =>
                    t.tier_label
                      ? `${t.tier_label} ×${t.count}`
                      : `×${t.count}`,
                  )
                  .join(", ")}
                {g.conditional && <ConditionalBadge text={g.conditional} />}
              </span>
              <span className="font-mono shrink-0">{g.bonus_display}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * Duplicate-curse heads-up for a vessel: curses stack, so two copies of the
 * same curse hurt twice. Sits beside the cumulative summary so it is visible
 * whether the card is expanded or collapsed. Curses the build asked for
 * (positive weight) never appear here — see stackedCurses().
 */
export function StackedCurseWarning({
  curses,
  className = "px-6 pb-3",
}: {
  curses: StackedCurse[]
  className?: string
}) {
  if (curses.length === 0) return null
  return (
    <div className={`${className} flex flex-wrap items-center gap-1.5`}>
      {curses.map((c) => (
        <Tooltip key={c.name}>
          <TooltipTrigger asChild>
            <span className="inline-flex items-center gap-1 rounded bg-destructive/10 px-1.5 py-px text-[10px] font-medium text-destructive">
              <AlertTriangle className="h-3 w-3 shrink-0" />
              <span className="truncate">{c.name}</span>
              <span className="font-mono">×{c.count}</span>
            </span>
          </TooltipTrigger>
          <TooltipContent className="max-w-xs">
            {c.count} copies of {c.name} in this loadout (slot
            {c.slots.length === 1 ? " " : "s "}
            {c.slots.map((s) => s + 1).join(", ")}). Curses stack in-game, so
            the penalty applies {c.count} times. Weight this curse in the build
            (or cap it) if you want the optimizer to avoid the pile-up.
          </TooltipContent>
        </Tooltip>
      ))}
    </div>
  )
}

export function VesselCard({
  vessel,
  defaultExpanded = false,
  highlighted = false,
  pinnedHandles = new Set(),
  effectMap = new Map(),
  enteredFingerprints,
  inventorySource,
  loadoutTarget,
  hasRequirements = false,
}: {
  vessel: VesselResult
  defaultExpanded?: boolean
  highlighted?: boolean
  pinnedHandles?: Set<number>
  effectMap?: Map<number, string>
  enteredFingerprints?: Set<string>
  /** When provided, enables the per-relic "strike" (X) controls. Omit to render
   *  a plain, read-only result card. */
  inventorySource?: InventorySource
  /** True when the build has explicit Required entries — gates the per-card
   *  covering check/X icon (hidden entirely for builds without any). */
  hasRequirements?: boolean
  /** When provided, shows a "Save as loadout" action that writes this vessel's
   *  relics into the save as an in-game relic loadout preset. ``existing`` lists
   *  this character's current loadouts (for the overwrite option). */
  loadoutTarget?: {
    slotIndex: number
    character: string
    existing?: {
      index: number
      name: string
      vessel_id: number
      ga_handles: number[]
    }[]
    /** ga_handle → relicContentKey for the profile's relics. Enables the
     *  content-equivalent tier of the "Saved" badge (same relics rearranged
     *  across same-color slots, or interchangeable duplicate copies). */
    relicContentByHandle?: Map<number, string>
  }
}) {
  const { showErrorToast } = useCustomToast()
  const [expanded, setExpanded] = useState(defaultExpanded)
  const [saveOpen, setSaveOpen] = useState(false)
  // Temporary, client-only strike state. Never persisted; reset whenever a fresh
  // optimization replaces the `vessel` prop (see effect below).
  const [workingVessel, setWorkingVessel] = useState<VesselResult>(vessel)
  const [excluded, setExcluded] = useState<Set<number>>(() => new Set())
  const [strikingSlot, setStrikingSlot] = useState<number | null>(null)
  const [noAltSlots, setNoAltSlots] = useState<Set<number>>(() => new Set())

  useEffect(() => {
    setWorkingVessel(vessel)
    setExcluded(new Set())
    setStrikingSlot(null)
    setNoAltSlots(new Set())
  }, [vessel])

  const isModified = workingVessel !== vessel
  const canStrike = inventorySource !== undefined

  // Recomputed from workingVessel so striking a relic updates the warning.
  const duplicateCurses = useMemo(
    () => stackedCurses(workingVessel.assignments),
    [workingVessel],
  )
  const duplicateCurseCounts = useMemo(
    () => new Map(duplicateCurses.map((c) => [c.name, c.count])),
    [duplicateCurses],
  )

  // Does this setup (same vessel + same non-empty relics) already exist as a
  // saved in-game loadout? Exact = same ga_handles (slot order ignored);
  // equivalent = same relic contents rearranged (see savedLoadoutMatch.ts).
  const savedMatch = useMemo(() => {
    const assigned = workingVessel.assignments
      .map((a) => a.relic as (RelicContent & { ga_handle?: number }) | null)
      .filter((r): r is RelicContent & { ga_handle?: number } => r !== null)
    return findSavedLoadoutMatch(
      workingVessel.vessel_id,
      assigned,
      loadoutTarget?.existing,
      loadoutTarget?.relicContentByHandle,
    )
  }, [loadoutTarget, workingVessel])

  const handleStrike = async (slotIndex: number) => {
    if (!inventorySource || strikingSlot !== null) return
    const struck = workingVessel.assignments[slotIndex]?.relic
    if (!struck) return
    const struckHandle = (struck as any).ga_handle as number
    // Freeze every other slot in its exact position (slot_index + relic), so the
    // backend re-fills only the struck slot and the rest of the layout holds.
    const locked = workingVessel.assignments
      .filter((a, i) => i !== slotIndex && a.relic)
      .map((a) => ({
        slot_index: a.slot_index,
        ga_handle: (a.relic as any).ga_handle as number,
      }))
    const nextExcluded = new Set(excluded).add(struckHandle)

    setStrikingSlot(slotIndex)
    try {
      const result = await fetchSlotAlternative({
        inventorySource,
        vessel_id: workingVessel.vessel_id,
        struck_slot_index: slotIndex,
        locked_slots: locked,
        excluded_ga_handles: [...nextExcluded],
      })
      const replacement = result?.assignments[slotIndex]?.relic ?? null
      if (result && replacement) {
        setWorkingVessel(result)
        setExcluded(nextExcluded)
      } else {
        // No relic can fill this slot once the struck one is gone — keep the
        // current arrangement, note it, and disable this slot's X. The relic
        // stays valid, so it is NOT added to the excluded set.
        setNoAltSlots((s) => new Set(s).add(slotIndex))
      }
    } catch (err) {
      showErrorToast(
        err instanceof Error ? err.message : "Failed to find an alternative",
      )
    } finally {
      setStrikingSlot(null)
    }
  }

  const resetVessel = () => {
    setWorkingVessel(vessel)
    setExcluded(new Set())
    setNoAltSlots(new Set())
  }

  return (
    <Card
      className={
        highlighted
          ? "ring-2 ring-primary/40 shadow-lg border-primary/30"
          : undefined
      }
    >
      <CardHeader
        className="cursor-pointer pb-3"
        onClick={() => setExpanded((v) => !v)}
      >
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-2">
            {highlighted && <Trophy className="h-4 w-4 text-gold shrink-0" />}
            <CardTitle className="text-base">
              {workingVessel.vessel_name}
            </CardTitle>
            {isModified && (
              <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                edited
              </Badge>
            )}
            {savedMatch && (
              <Badge
                variant="secondary"
                className="text-[10px] px-1.5 py-0 gap-1"
                title={
                  (savedMatch.equivalent
                    ? `Same relics as your loadout "${savedMatch.loadout.name || "(unnamed)"}" — arranged differently across interchangeable slots, which doesn't change the result`
                    : `Already saved as "${savedMatch.loadout.name || "(unnamed)"}"`) +
                  // index -1 = a staged add: saved in the app, not written to
                  // the save file until the user exports.
                  (savedMatch.loadout.index < 0
                    ? " — saved in the app, not exported to your save yet"
                    : " (in-game)")
                }
              >
                <BookMarked className="h-3 w-3" />
                {savedMatch.equivalent ? "Saved ≈" : "Saved"}
                {savedMatch.loadout.name ? `: ${savedMatch.loadout.name}` : ""}
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-2">
            {loadoutTarget && (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 px-2"
                onClick={(e) => {
                  e.stopPropagation()
                  setSaveOpen(true)
                }}
              >
                <BookMarked className="h-3.5 w-3.5 mr-1" />
                Save as loadout
              </Button>
            )}
            {isModified && (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 px-2"
                onClick={(e) => {
                  e.stopPropagation()
                  resetVessel()
                }}
              >
                <RotateCcw className="h-3.5 w-3.5 mr-1" />
                Reset
              </Button>
            )}
            {hasRequirements &&
              (workingVessel.meets_requirements ? (
                <CheckCircle2 className="h-4 w-4 text-green-500" />
              ) : (
                <XCircle className="h-4 w-4 text-destructive" />
              ))}
            <Badge variant="secondary">{workingVessel.total_score} pts</Badge>
            {expanded ? (
              <ChevronUp className="h-4 w-4 text-muted-foreground" />
            ) : (
              <ChevronDown className="h-4 w-4 text-muted-foreground" />
            )}
          </div>
        </div>
        <p className="text-xs text-muted-foreground">
          {workingVessel.vessel_character}
        </p>
      </CardHeader>
      <CumulativeSummary groups={workingVessel.cumulative_effects} />
      <StackedCurseWarning curses={duplicateCurses} />
      {expanded && (
        <CardContent className="pt-0">
          <Separator className="mb-3" />
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {workingVessel.assignments.map((slot) => (
              <SlotCard
                key={slot.slot_index}
                slot={slot}
                isPinned={
                  slot.relic != null &&
                  pinnedHandles.has((slot.relic as any).ga_handle)
                }
                isNew={
                  slot.relic != null &&
                  (enteredFingerprints?.has(relicKey(slot.relic as any)) ??
                    false)
                }
                onStrike={
                  canStrike ? () => handleStrike(slot.slot_index) : undefined
                }
                isStriking={strikingSlot === slot.slot_index}
                busy={strikingSlot !== null}
                noAlternative={noAltSlots.has(slot.slot_index)}
                stackedCurseCounts={duplicateCurseCounts}
              />
            ))}
          </div>
          {!workingVessel.meets_requirements &&
            (workingVessel.missing_requirements?.length ?? 0) > 0 && (
              <p className="text-xs text-destructive mt-3">
                Missing required effects:{" "}
                {(workingVessel.missing_requirements ?? [])
                  .map((m) =>
                    typeof m === "number"
                      ? (effectMap.get(m) ?? `Effect ${m}`)
                      : m,
                  )
                  .join(", ")}
              </p>
            )}
        </CardContent>
      )}
      {loadoutTarget && (
        <SaveLoadoutDialog
          open={saveOpen}
          onOpenChange={setSaveOpen}
          vessel={workingVessel}
          target={loadoutTarget}
        />
      )}
    </Card>
  )
}

/** Dialog that writes the vessel's relics into the save as a new in-game relic
 *  loadout preset (op=add). The user re-imports the downloaded .sl2 in-game. */
function SaveLoadoutDialog({
  open,
  onOpenChange,
  vessel,
  target,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  vessel: VesselResult
  target: {
    slotIndex: number
    character: string
    existing?: { index: number; name: string }[]
  }
}) {
  const { showSuccessToast } = useCustomToast()
  const [mode, setMode] = useState<"add" | "overwrite">("add")
  const [name, setName] = useState("")
  // Selected replace target: "idx-<presetIndex>" | "add-<stagedOpId>".
  const [overwriteKey, setOverwriteKey] = useState<string>("")
  // A same-named live loadout the user has been warned about, awaiting their
  // "replace it" / "add anyway" answer. Null until they try to add.
  const [conflict, setConflict] = useState<ReplaceTarget | null>(null)

  // The LIVE preset list, not the raw save's (staged deletes/renames/reset/
  // adds composed) — same world the Loadouts page renders. All semantics live
  // in the pendingChanges selectors so they stay unit-tested.
  const pending = usePendingSlot(target.slotIndex)
  const targets = replaceTargets(
    target.existing ?? [],
    pending,
    target.character,
  )
  const keyOf = (t: ReplaceTarget) =>
    t.kind === "existing" ? `idx-${t.index}` : `add-${t.opId}`

  // Relics ordered by slot index (0..5), 0 for empty slots.
  const gaHandles = [...vessel.assignments]
    .sort((a, b) => a.slot_index - b.slot_index)
    .map((a) => (a.relic as { ga_handle?: number } | null)?.ga_handle ?? 0)

  const valid = mode === "add" ? name.trim().length > 0 : overwriteKey !== ""

  function close() {
    onOpenChange(false)
    setName("")
    setOverwriteKey("")
    setConflict(null)
  }

  function commitAdd() {
    addLoadoutOp(target.slotIndex, {
      kind: "add",
      character: target.character,
      vessel_id: vessel.vessel_id,
      ga_handles: gaHandles,
      name: name.trim(),
      vesselName: vessel.vessel_name,
    })
    showSuccessToast(
      `Added loadout "${name.trim()}" — export from the Changes panel to save it.`,
    )
    close()
  }

  function commitReplace(t: ReplaceTarget) {
    queueReplaceLoadout(target.slotIndex, t, {
      character: target.character,
      vessel_id: vessel.vessel_id,
      ga_handles: gaHandles,
      vesselName: vessel.vessel_name,
    })
    showSuccessToast(
      `Replaced ${t.kind === "staged-add" ? "staged loadout " : ""}"${
        t.name || "loadout"
      }" — export from the Changes panel to save it.`,
    )
    close()
  }

  function doQueue() {
    if (!valid) return
    if (mode === "add") {
      // The game allows duplicate preset names, so a clash is a question, not
      // an error: warn once, then let them replace or keep both.
      const clash = findLoadoutNameConflict(targets, name)
      if (clash) {
        setConflict(clash)
        return
      }
      commitAdd()
    } else {
      const t = targets.find((x) => keyOf(x) === overwriteKey)
      if (t) commitReplace(t)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => (v ? onOpenChange(true) : close())}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Save as in-game loadout</DialogTitle>
          <DialogDescription>
            Adds a loadout for <strong>{target.character}</strong> on{" "}
            <strong>{vessel.vessel_name}</strong>. It shows up on the Loadouts
            page right away; export from the Changes panel to write it to your
            save.
          </DialogDescription>
        </DialogHeader>

        {targets.length > 0 && (
          <div className="flex gap-2">
            <Button
              variant={mode === "add" ? "default" : "outline"}
              size="sm"
              onClick={() => {
                setMode("add")
                setConflict(null)
              }}
            >
              Create new
            </Button>
            <Button
              variant={mode === "overwrite" ? "default" : "outline"}
              size="sm"
              onClick={() => {
                setMode("overwrite")
                setConflict(null)
              }}
            >
              Replace existing
            </Button>
          </div>
        )}

        {mode === "add" ? (
          <div className="space-y-1">
            <Input
              placeholder="Loadout name"
              value={name}
              maxLength={18}
              onChange={(e) => {
                setName(e.target.value)
                setConflict(null)
              }}
              onKeyDown={(e) => e.key === "Enter" && doQueue()}
            />
            <div className="text-xs text-muted-foreground">
              {name.length}/18
            </div>
            {conflict && (
              <p className="text-xs text-amber-600 dark:text-amber-500">
                {target.character} already has a
                {conflict.kind === "staged-add" ? " staged" : ""} loadout named
                “{conflict.name || "(unnamed)"}”. Replace it, or add a second
                one with the same name?
              </p>
            )}
          </div>
        ) : (
          <Select value={overwriteKey} onValueChange={setOverwriteKey}>
            <SelectTrigger>
              <SelectValue placeholder="Choose a loadout to replace" />
            </SelectTrigger>
            <SelectContent>
              {targets.map((t) => (
                <SelectItem key={keyOf(t)} value={keyOf(t)}>
                  {t.name || "(unnamed)"}
                  {t.kind === "staged-add" ? " (staged)" : ""}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={close}>
            Cancel
          </Button>
          {conflict ? (
            <>
              <Button variant="outline" onClick={commitAdd}>
                Add anyway
              </Button>
              <Button onClick={() => commitReplace(conflict)}>
                Replace it
              </Button>
            </>
          ) : (
            <Button onClick={doQueue} disabled={!valid}>
              {mode === "add" ? "Add loadout" : "Replace loadout"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// --- Save-diff banner (shown above results after a re-optimize) ---

export function ChangeBanner({
  change,
  effectMap,
}: {
  change?: BuildChange | null
  effectMap: Map<number, string>
}) {
  // Narrate what the user did not do themselves: a newer save, or relics they
  // bought in Relic Rites. Build edits and game-data bumps re-baseline
  // silently, as does run-to-run search noise (no causes at all).
  if (!isChangeNews(change)) return null
  const d = describeBuildChange(change)
  if (!d) return null
  const Icon = d.icon

  return (
    <div className={`rounded-md border px-3 py-2 text-sm ${d.boxClass}`}>
      <div
        className="flex items-center gap-2"
        title={rawScoreTooltip(d.rawScore)}
      >
        <Icon className="h-4 w-4 shrink-0" />
        <span>Since your last save: {d.headline}</span>
        {d.reliable === false && (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="cursor-help text-xs underline decoration-dotted underline-offset-2 opacity-70">
                (approximate)
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-[16rem]">
              The optimizer hit its time limit, so this is the best layout it
              found — not a proven optimum. The numbers may shift slightly on a
              full re-run.
            </TooltipContent>
          </Tooltip>
        )}
      </div>
      <ChangeRelicGroups groups={d.groups} effectMap={effectMap} max={4} />
      {d.note && <p className="mt-1 text-xs opacity-80">{d.note}</p>}
    </div>
  )
}

/** Separator inserted above the first result that misses required effects,
 *  splitting the list into covering results and the flagged tail. */
export function MissingRequirementsSeparator() {
  return (
    <div className="flex items-center gap-3 pt-2">
      <div className="flex-1 h-px bg-destructive/40" />
      <span className="text-xs text-destructive">
        the following are missing required effect(s)
      </span>
      <div className="flex-1 h-px bg-destructive/40" />
    </div>
  )
}

/** Banner shown when not a single result covers the build's Required row. */
export function NoCoveringResultsBanner() {
  return (
    <div className="flex items-center gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-400">
      <AlertTriangle className="h-4 w-4 shrink-0" />
      <span>
        No optimal results include the required relic properties — these are the
        closest matches.
      </span>
    </div>
  )
}
