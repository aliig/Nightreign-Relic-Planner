import { useState } from "react"
import { ChevronDown, ChevronUp, CheckCircle2, XCircle, Trophy, Pin } from "lucide-react"

import type { VesselResult } from "@/client"
import { COLOR_HEX, RelicNameCell } from "@/components/RelicDisplay"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"

// --- Types ---

export type SlotAssignment = VesselResult["assignments"][number]

export interface OptimizeProgress {
  vessel: number
  total: number
  name: string
}

// --- Helpers ---

export function getBreakdownColor(b: Record<string, unknown>): string | undefined {
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

export function cacheKey(...parts: (string | number | null | undefined)[]): string {
  return parts.map((p) => String(p ?? "")).join(":")
}

// --- SSE streaming ---

export async function runOptimizeStream(
  requestBody: Record<string, unknown>,
  onProgress: (p: OptimizeProgress) => void,
): Promise<VesselResult[]> {
  const token = localStorage.getItem("access_token")
  const headers: HeadersInit = { "Content-Type": "application/json" }
  if (token) (headers as Record<string, string>)["Authorization"] = `Bearer ${token}`

  const response = await fetch("/api/v1/optimize/stream", {
    method: "POST",
    headers,
    body: JSON.stringify(requestBody),
  })

  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: "Optimization failed" }))
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
        onProgress({ vessel: payload.vessel, total: payload.total, name: payload.name })
      } else if (payload.type === "result") {
        return payload.data as VesselResult[]
      } else if (payload.type === "error") {
        throw new Error(payload.detail ?? "Optimization failed")
      }
    }
  }

  throw new Error("Stream ended without a result")
}

// --- Components ---

export function SlotCard({ slot, isPinned = false }: { slot: SlotAssignment; isPinned?: boolean }) {
  const relic = slot.relic
  const effects = slot.breakdown?.filter((b: Record<string, unknown>) => !b.is_curse) ?? []
  const curses = slot.breakdown?.filter((b: Record<string, unknown>) => b.is_curse) ?? []

  return (
    <div className={`rounded-md border p-3 space-y-1.5${isPinned ? " border-primary/40 bg-primary/5" : ""}`}>
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
          {isPinned && (
            <span title="Pinned relic">
              <Pin className="h-3 w-3 text-primary shrink-0" />
            </span>
          )}
          <span className="text-xs font-mono font-semibold">{slot.score} pts</span>
        </div>
      </div>
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
                <div key={i} className="flex items-center justify-between text-xs">
                  <span
                    className="truncate"
                    style={{ color: getBreakdownColor(b) }}
                  >
                    {b.name as string}
                    {b.redundant ? " (redundant)" : ""}
                  </span>
                  <span className="font-mono ml-2 shrink-0">
                    {(b.score as number) >= 0 ? "+" : ""}{b.score as number}
                  </span>
                </div>
              ))}
            </div>
          )}
          {curses.length > 0 && (
            <div className="mt-1.5 pt-1.5 border-t border-destructive/20">
              <div className="space-y-0.5">
                {curses.map((b: Record<string, unknown>, i: number) => (
                  <div key={i} className="flex items-center justify-between text-xs">
                    <span className="truncate text-destructive/80">
                      {b.name as string}
                      {b.redundant ? " (redundant)" : ""}
                    </span>
                    <span className="font-mono ml-2 shrink-0 text-destructive/80">
                      {(b.score as number) >= 0 ? "+" : ""}{b.score as number}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      ) : (
        <p className="text-xs text-muted-foreground italic">No relic assigned</p>
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
}: {
  vessel: VesselResult
  defaultExpanded?: boolean
  highlighted?: boolean
  pinnedHandles?: Set<number>
  effectMap?: Map<number, string>
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)

  return (
    <Card
      className={highlighted ? "ring-2 ring-primary/40 shadow-lg border-primary/30" : undefined}
    >
      <CardHeader
        className="cursor-pointer pb-3"
        onClick={() => setExpanded((v) => !v)}
      >
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-2">
            {highlighted && <Trophy className="h-4 w-4 text-gold shrink-0" />}
            <CardTitle className="text-base">{vessel.vessel_name}</CardTitle>
          </div>
          <div className="flex items-center gap-2">
            {vessel.meets_requirements ? (
              <CheckCircle2 className="h-4 w-4 text-green-500" />
            ) : (
              <XCircle className="h-4 w-4 text-destructive" />
            )}
            <Badge variant="secondary">{vessel.total_score} pts</Badge>
            {expanded ? (
              <ChevronUp className="h-4 w-4 text-muted-foreground" />
            ) : (
              <ChevronDown className="h-4 w-4 text-muted-foreground" />
            )}
          </div>
        </div>
        <p className="text-xs text-muted-foreground">{vessel.vessel_character}</p>
      </CardHeader>
      {expanded && (
        <CardContent className="pt-0">
          <Separator className="mb-3" />
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {vessel.assignments.map((slot) => (
              <SlotCard
                key={slot.slot_index}
                slot={slot}
                isPinned={slot.relic != null && pinnedHandles.has((slot.relic as any).ga_handle)}
              />
            ))}
          </div>
          {!vessel.meets_requirements && vessel.missing_requirements?.length > 0 && (
            <p className="text-xs text-destructive mt-3">
              Missing required effects:{" "}
              {vessel.missing_requirements
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
