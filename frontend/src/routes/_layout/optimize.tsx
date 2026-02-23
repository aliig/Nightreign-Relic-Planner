import { createFileRoute, Link } from "@tanstack/react-router"
import { useMutation, useSuspenseQuery } from "@tanstack/react-query"
import { Suspense, useState } from "react"
import { ChevronDown, ChevronUp, CheckCircle2, XCircle, Trophy, Pin } from "lucide-react"

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
import { isLoggedIn } from "@/hooks/useAuth"
import { useLocalBuilds } from "@/hooks/useLocalBuilds"
import useCustomToast from "@/hooks/useCustomToast"
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
  bonus: "#9966CC", avoid: "#888888", blacklist: "#FF8C00",
}

type VesselResult = Awaited<ReturnType<typeof OptimizeService.runOptimize>>[number]
type SlotAssignment = VesselResult["assignments"][number]

function SlotCard({ slot, isPinned = false }: { slot: SlotAssignment; isPinned?: boolean }) {
  const relic = slot.relic

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
          {isPinned && <Pin className="h-3 w-3 text-primary shrink-0" title="Pinned relic" />}
          <span className="text-xs font-mono font-semibold">{slot.score} pts</span>
        </div>
      </div>
      {relic ? (
        <>
          <p className="text-sm font-medium">{relic.name}</p>
          <p className="text-xs text-muted-foreground">{relic.tier} · {relic.color}</p>
          {slot.breakdown?.length > 0 && (
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

function VesselCard({
  vessel,
  defaultExpanded = false,
  highlighted = false,
  pinnedHandles = new Set(),
}: {
  vessel: VesselResult
  defaultExpanded?: boolean
  highlighted?: boolean
  pinnedHandles?: Set<number>
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
            {highlighted && <Trophy className="h-4 w-4 text-yellow-500 shrink-0" />}
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
              Missing required effects: {vessel.missing_requirements.join(", ")}
            </p>
          )}
        </CardContent>
      )}
    </Card>
  )
}

// --- Authenticated optimizer (DB-backed) ---

function AuthOptimizeForm() {
  const { showErrorToast } = useCustomToast()
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

  const selectedBuild = builds.find((b) => b.id === buildId)
  const pinnedHandles = new Set<number>(selectedBuild?.pinned_relics ?? [])

  const optimizeMutation = useMutation({
    mutationFn: () =>
      OptimizeService.runOptimize({
        requestBody: { build_id: buildId, character_id: characterId },
      }),
    onSuccess: (data) => setResults(data),
    onError: handleError.bind(showErrorToast),
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
          {results.map((vessel, index) => (
            <VesselCard
              key={`${vessel.vessel_id}-${vessel.total_score}`}
              vessel={vessel}
              defaultExpanded={index === 0}
              highlighted={index === 0}
              pinnedHandles={pinnedHandles}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// --- Anonymous optimizer (inline mode) ---

interface SessionCharacter {
  slot_index: number
  name: string
  relics: Array<Record<string, unknown>>
}

function AnonOptimizeForm() {
  const { showErrorToast } = useCustomToast()
  const { builds } = useLocalBuilds()

  const allChars: SessionCharacter[] = JSON.parse(
    sessionStorage.getItem("parsedCharacters") ?? "[]"
  )

  const defaultSlot = (() => {
    try {
      const c = JSON.parse(sessionStorage.getItem("selectedCharacter") ?? "null")
      return c?.slot_index ?? allChars[0]?.slot_index ?? null
    } catch { return allChars[0]?.slot_index ?? null }
  })()

  const [selectedSlot, setSelectedSlot] = useState<number | null>(defaultSlot)
  const char = allChars.find((c) => c.slot_index === selectedSlot) ?? allChars[0] ?? null

  const handleCharChange = (slotStr: string) => {
    const slot = Number(slotStr)
    setSelectedSlot(slot)
    const picked = allChars.find((c) => c.slot_index === slot)
    if (picked) sessionStorage.setItem("selectedCharacter", JSON.stringify(picked))
  }

  const [buildId, setBuildId] = useState(builds[0]?.id ?? "")
  const [results, setResults] = useState<VesselResult[]>([])

  const selectedBuild = builds.find((b) => b.id === buildId)
  const pinnedHandles = new Set<number>(selectedBuild?.pinned_relics ?? [])

  const optimizeMutation = useMutation({
    mutationFn: () => {
      const build = builds.find((b) => b.id === buildId)
      if (!build || !char) throw new Error("Missing build or character data")

      // ParsedRelicData uses flat effect_1/2/3 fields; OwnedRelic expects arrays
      const relics = char.relics.map((r: any) => ({
        ga_handle: r.ga_handle,
        item_id: r.item_id,
        real_id: r.real_id,
        color: r.color,
        effects: [r.effect_1, r.effect_2, r.effect_3],
        curses: [r.curse_1, r.curse_2, r.curse_3],
        is_deep: r.is_deep,
        name: r.name,
        tier: r.tier,
      }))

      return OptimizeService.runOptimize({
        requestBody: {
          build: {
            id: build.id,
            name: build.name,
            character: build.character,
            tiers: build.tiers,
            family_tiers: build.family_tiers,
            include_deep: build.include_deep,
            curse_max: build.curse_max,
            tier_weights: build.tier_weights ?? null,
            pinned_relics: build.pinned_relics ?? [],
          } as any,
          relics: relics as any,
        },
      })
    },
    onSuccess: (data) => setResults(data),
    onError: handleError.bind(showErrorToast),
  })

  if (allChars.length === 0) {
    return (
      <p className="text-sm text-muted-foreground py-8">
        No inventory loaded.{" "}
        <Link to="/upload" className="underline">Upload a save file</Link> first.
      </p>
    )
  }

  if (builds.length === 0) {
    return (
      <p className="text-sm text-muted-foreground py-8">
        No builds found.{" "}
        <Link to="/builds" className="underline">Create a build</Link> first.
      </p>
    )
  }

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
        {allChars.length > 1 && (
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Character</label>
            <Select value={String(char?.slot_index ?? "")} onValueChange={handleCharChange}>
              <SelectTrigger className="w-56">
                <SelectValue placeholder="Select character" />
              </SelectTrigger>
              <SelectContent>
                {allChars.map((c) => (
                  <SelectItem key={c.slot_index} value={String(c.slot_index)}>
                    {c.name} (Slot {c.slot_index})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
        <Button
          onClick={() => optimizeMutation.mutate()}
          disabled={!buildId || optimizeMutation.isPending}
        >
          {optimizeMutation.isPending ? "Optimizing…" : "Optimize"}
        </Button>
      </div>

      <p className="text-xs text-muted-foreground border rounded-md px-3 py-2 bg-muted/40">
        Running in session mode with data from your uploaded save.{" "}
        <Link to="/login" search={{ redirect: "/optimize" }} className="underline">
          Sign in
        </Link>{" "}
        to persist builds and inventory across devices.
      </p>

      {results.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-lg font-medium">
            Top {results.length} vessel{results.length !== 1 ? "s" : ""}
          </h2>
          {results.map((vessel, index) => (
            <VesselCard
              key={`${vessel.vessel_id}-${vessel.total_score}`}
              vessel={vessel}
              defaultExpanded={index === 0}
              highlighted={index === 0}
              pinnedHandles={pinnedHandles}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// --- Page ---

function OptimizePage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Optimize</h1>
        <p className="text-muted-foreground mt-1">
          Find the best vessel–relic assignments for your build.
        </p>
      </div>
      <Suspense fallback={<Skeleton className="h-32 w-full" />}>
        {isLoggedIn() ? <AuthOptimizeForm /> : <AnonOptimizeForm />}
      </Suspense>
    </div>
  )
}
