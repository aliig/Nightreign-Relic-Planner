import { createFileRoute } from "@tanstack/react-router"
import { useMutation, useSuspenseQuery } from "@tanstack/react-query"
import { Suspense, useState } from "react"
import { ChevronDown, ChevronUp, CheckCircle2, XCircle } from "lucide-react"

import { BuildsService, OptimizeService, SavesService } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Separator } from "@/components/ui/separator"
import useAuth from "@/hooks/useAuth"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/optimize")({
  component: OptimizePage,
  head: () => ({
    meta: [{ title: "Optimize - Nightreign Relic Planner" }],
  }),
})

const COLOR_HEX: Record<string, string> = {
  Red: "#FF4444", Blue: "#4488FF", Yellow: "#B8860B", Green: "#44BB44", White: "#AAAAAA",
}

const TIER_COLORS: Record<string, string> = {
  required: "#FF4444", preferred: "#4488FF", nice_to_have: "#44BB88",
  avoid: "#888888", blacklist: "#FF8C00",
}

type VesselResult = Awaited<ReturnType<typeof OptimizeService.runOptimize>>[number]
type SlotAssignment = VesselResult["assignments"][number]

function SlotCard({ slot }: { slot: SlotAssignment }) {
  const [open, setOpen] = useState(false)
  const relic = slot.relic

  return (
    <div className="rounded-md border p-3 space-y-1.5">
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
        <span className="text-xs font-mono font-semibold">{slot.score} pts</span>
      </div>
      {relic ? (
        <>
          <p className="text-sm font-medium">{relic.name}</p>
          <p className="text-xs text-muted-foreground">{relic.tier} · {relic.color}</p>
          {slot.breakdown?.length > 0 && (
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground mt-1"
            >
              {open ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
              Breakdown
            </button>
          )}
          {open && (
            <div className="space-y-0.5 mt-1">
              {slot.breakdown.map((b: Record<string, unknown>, i: number) => (
                <div key={i} className="flex items-center justify-between text-xs">
                  <span
                    className="truncate"
                    style={{ color: TIER_COLORS[b.tier as string] ?? undefined }}
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
        </>
      ) : (
        <p className="text-xs text-muted-foreground italic">No relic assigned</p>
      )}
    </div>
  )
}

function VesselCard({ vessel }: { vessel: VesselResult }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <Card>
      <CardHeader
        className="cursor-pointer pb-3"
        onClick={() => setExpanded((v) => !v)}
      >
        <div className="flex items-center justify-between flex-wrap gap-2">
          <CardTitle className="text-base">{vessel.vessel_name}</CardTitle>
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
              <SlotCard key={slot.slot_index} slot={slot} />
            ))}
          </div>
          {!vessel.meets_requirements && vessel.missing_requirements?.length > 0 && (
            <p className="text-xs text-destructive mt-3">
              Missing required effects: {vessel.missing_requirements.join(", ")}
            </p>
          )}
        </CardContent>
      )}
    </Card>
  )
}

function AuthOptimizeForm() {
  const { data: buildsData } = useSuspenseQuery({
    queryKey: ["builds"],
    queryFn: () => BuildsService.listBuilds(),
  })
  const { data: charsData } = useSuspenseQuery({
    queryKey: ["characters"],
    queryFn: () => SavesService.listCharacters(),
  })

  const builds = buildsData?.data ?? []
  const chars = charsData?.data ?? []

  const [buildId, setBuildId] = useState(builds[0]?.id ?? "")
  const [characterId, setCharacterId] = useState(chars[0]?.id ?? "")
  const [results, setResults] = useState<VesselResult[]>([])

  const optimizeMutation = useMutation({
    mutationFn: () =>
      OptimizeService.runOptimize({
        requestBody: { build_id: buildId, character_id: characterId },
      }),
    onSuccess: (data) => setResults(data),
    onError: handleError,
  })

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap gap-3 items-end">
        <div className="space-y-1.5">
          <label className="text-sm font-medium">Build</label>
          <Select value={buildId} onValueChange={setBuildId}>
            <SelectTrigger className="w-56">
              <SelectValue placeholder="Select build" />
            </SelectTrigger>
            <SelectContent>
              {builds.map((b) => (
                <SelectItem key={b.id} value={b.id}>{b.name} ({b.character})</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <label className="text-sm font-medium">Character</label>
          <Select value={characterId} onValueChange={setCharacterId}>
            <SelectTrigger className="w-56">
              <SelectValue placeholder="Select character" />
            </SelectTrigger>
            <SelectContent>
              {chars.map((c) => (
                <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button
          onClick={() => optimizeMutation.mutate()}
          disabled={!buildId || !characterId || optimizeMutation.isPending}
        >
          {optimizeMutation.isPending ? "Optimizing…" : "Optimize"}
        </Button>
      </div>

      {builds.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No builds found. <a href="/builds" className="underline">Create a build</a> first.
        </p>
      )}
      {chars.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No inventory found. <a href="/upload" className="underline">Upload a save file</a> first.
        </p>
      )}

      {results.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-lg font-medium">
            Top {results.length} vessel{results.length !== 1 ? "s" : ""}
          </h2>
          {results.map((vessel) => (
            <VesselCard key={`${vessel.vessel_id}-${vessel.total_score}`} vessel={vessel} />
          ))}
        </div>
      )}
    </div>
  )
}

function AnonOptimize() {
  return (
    <p className="text-sm text-muted-foreground py-8">
      Please <a href="/login" className="underline">sign in</a> to use the optimizer.
      Anonymous optimization via inline data will be supported in a future update.
    </p>
  )
}

function OptimizePage() {
  const { user } = useAuth()

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Optimize</h1>
        <p className="text-muted-foreground mt-1">
          Find the best vessel–relic assignments for your build.
        </p>
      </div>
      <Suspense fallback={<Skeleton className="h-32 w-full" />}>
        {user ? <AuthOptimizeForm /> : <AnonOptimize />}
      </Suspense>
    </div>
  )
}
