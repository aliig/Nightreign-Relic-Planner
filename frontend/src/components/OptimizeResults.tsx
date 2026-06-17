import {
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
import { useEffect, useState } from "react"

import {
  ApiError,
  type BuildChange,
  OptimizeService,
  type SlotAlternativeRequest,
  type VesselResult,
} from "@/client"
import { COLOR_HEX, RelicNameCell } from "@/components/RelicDisplay"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import useCustomToast from "@/hooks/useCustomToast"
import {
  describeBuildChange,
  rawScoreTooltip,
  relicSummary,
} from "@/lib/buildChange"

// --- Types ---

export type SlotAssignment = VesselResult["assignments"][number]

export interface OptimizeProgress {
  vessel: number
  total: number
  name: string
}

// --- Helpers ---

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

// --- Result cache (persists across route navigations, clears on page reload) ---

export const resultCache = new Map<string, VesselResult[]>()

export function cacheKey(
  ...parts: (string | number | null | undefined)[]
): string {
  return parts.map((p) => String(p ?? "")).join(":")
}

// --- Change highlighting (save-diff) ---

export function relicKey(r: {
  real_id?: number
  effects?: number[] | null
  curses?: number[] | null
}): string {
  return `${r.real_id ?? ""}:${(r.effects ?? []).join(",")}:${(r.curses ?? []).join(",")}`
}

/** Content keys of relics that entered the best arrangement (for "NEW" badges).
 *  Only meaningful for a save-driven change — a build edit re-baselines and is
 *  not a "since last save" event, so nothing is tagged NEW for it. */
export function enteredKeys(change?: BuildChange | null): Set<string> {
  const keys = new Set<string>()
  if (!change || change.cause !== "relics") return keys
  for (const r of change.entered ?? []) keys.add(relicKey(r))
  return keys
}

// --- SSE streaming ---

export async function runOptimizeStream(
  requestBody: Record<string, unknown>,
  onProgress: (p: OptimizeProgress) => void,
  onChange?: (change: BuildChange | null) => void,
): Promise<VesselResult[]> {
  const token = localStorage.getItem("access_token")
  const headers: HeadersInit = { "Content-Type": "application/json" }
  if (token)
    (headers as Record<string, string>).Authorization = `Bearer ${token}`

  const response = await fetch("/api/v1/optimize/stream", {
    method: "POST",
    headers,
    body: JSON.stringify(requestBody),
  })

  if (!response.ok) {
    const err = await response
      .json()
      .catch(() => ({ detail: "Optimization failed" }))
    throw new Error(err.detail ?? "Optimization failed")
  }

  const reader = response.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // SSE events are separated by double newlines
    const parts = buffer.split("\n\n")
    buffer = parts.pop() ?? ""

    for (const part of parts) {
      const dataLine = part.split("\n").find((l) => l.startsWith("data: "))
      if (!dataLine) continue
      const payload = JSON.parse(dataLine.slice(6))

      if (payload.type === "progress") {
        onProgress({
          vessel: payload.vessel,
          total: payload.total,
          name: payload.name,
        })
      } else if (payload.type === "result") {
        onChange?.((payload.change ?? null) as BuildChange | null)
        return payload.data as VesselResult[]
      } else if (payload.type === "error") {
        throw new Error(payload.detail ?? "Optimization failed")
      }
    }
  }

  throw new Error("Stream ended without a result")
}

// --- Single-slot re-optimization ("strike a relic") ---

/** How the optimizer should source the relic inventory for a re-optimize:
 *  DB mode (authenticated build + profile) or inline mode (anonymous). */
export type InventorySource =
  | { build_id: string; profile_id: string }
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
}: {
  slot: SlotAssignment
  isPinned?: boolean
  isNew?: boolean
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
                    {b.redundant ? " (redundant)" : ""}
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
                      {b.redundant ? " (redundant)" : ""}
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
 * Cumulative stacked-effect summary shown under each vessel: the single biggest
 * bonus at a glance, plus a "see all" toggle listing every family's cumulative %.
 * Rendered outside the (clickable) CardHeader so it stays visible when collapsed
 * and its toggle never triggers the card's expand/collapse.
 */
function CumulativeSummary({
  groups,
}: {
  groups: VesselResult["cumulative_effects"]
}) {
  const [open, setOpen] = useState(false)
  if (!groups || groups.length === 0) return null
  const top = groups.find((g) => g.is_top) ?? groups[0]
  return (
    <div className="px-6 pb-3 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 min-w-0">
          <Zap className="h-3.5 w-3.5 text-amber-500 shrink-0" />
          <span className="font-medium truncate">{top.family}</span>
          <span className="font-mono text-muted-foreground shrink-0">
            {top.bonus_display}
          </span>
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
                {g.tiers.map((t) => `${t.tier_label} ×${t.count}`).join(", ")}
              </span>
              <span className="font-mono shrink-0">{g.bonus_display}</span>
            </div>
          ))}
        </div>
      )}
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
}) {
  const { showErrorToast } = useCustomToast()
  const [expanded, setExpanded] = useState(defaultExpanded)
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
          </div>
          <div className="flex items-center gap-2">
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
            {workingVessel.meets_requirements ? (
              <CheckCircle2 className="h-4 w-4 text-green-500" />
            ) : (
              <XCircle className="h-4 w-4 text-destructive" />
            )}
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
    </Card>
  )
}

// --- Save-diff banner (shown above results after a re-optimize) ---

export function ChangeBanner({ change }: { change?: BuildChange | null }) {
  // Only narrate changes a newer save caused. Build edits (cause "build_edit" /
  // "mixed") and run-to-run search noise (cause null) re-baseline silently.
  if (!change || change.cause !== "relics") return null
  const d = describeBuildChange(change)
  if (!d) return null
  const Icon = d.icon

  return (
    <div
      className={`flex items-center gap-2 rounded-md border px-3 py-2 text-sm ${d.boxClass}`}
      title={rawScoreTooltip(d.rawScore)}
    >
      <Icon className="h-4 w-4 shrink-0" />
      <span>
        Since your last save: {d.headline}
        {d.relics ? ` — ${d.relics.verb} ${relicSummary(d.relics)}` : ""}.
      </span>
      {d.reliable === false && (
        <span className="text-xs opacity-70">(approximate)</span>
      )}
    </div>
  )
}
