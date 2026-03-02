import { createFileRoute, Link, useParams } from "@tanstack/react-router"
import { useSuspenseQuery } from "@tanstack/react-query"
import { Suspense, useEffect, useMemo, useState } from "react"

import { BuildsService, GameService, SavesService } from "@/client"
import type { VesselResult } from "@/client"
import { buildEffectMap } from "@/components/RelicDisplay"
import {
  VesselCard,
  runOptimizeStream,
  resultCache,
  cacheKey,
  type OptimizeProgress,
} from "@/components/OptimizeResults"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { isLoggedIn } from "@/hooks/useAuth"
import { useLocalBuilds } from "@/hooks/useLocalBuilds"
import useCustomToast from "@/hooks/useCustomToast"
import { useSaveStatus, getAnonUploadMeta } from "@/hooks/useSaveStatus"

export const Route = createFileRoute("/_layout/builds/$buildId/optimize")({
  component: BuildOptimizePage,
  head: () => ({
    meta: [{ title: "Optimize Build - Nightreign Relic Planner" }],
  }),
})

// --- Authenticated optimizer (DB-backed, build from route) ---

function AuthOptimizeForm({ buildId }: { buildId: string }) {
  const { showErrorToast } = useCustomToast()
  const { data: buildRaw } = useSuspenseQuery({
    queryKey: ["builds", buildId],
    queryFn: () => BuildsService.getBuild({ buildId }),
  })
  const { data: charsData } = useSuspenseQuery({
    queryKey: ["characters"],
    queryFn: () => SavesService.listCharacters(),
  })
  const { data: effectsData } = useSuspenseQuery({
    queryKey: ["game", "effects"],
    queryFn: () => GameService.getEffects(),
    staleTime: Infinity,
  })
  const { status: saveStatus } = useSaveStatus()
  const effectMap = useMemo(() => buildEffectMap((effectsData ?? []) as unknown[]), [effectsData])

  const selectedBuild = buildRaw as any
  const chars = charsData?.data ?? []

  const [characterId, setCharacterId] = useState(chars[0]?.id ?? "")

  const pinnedHandles = new Set<number>(selectedBuild?.pinned_relics ?? [])
  const key = cacheKey("auth", buildId, selectedBuild?.updated_at, characterId, saveStatus?.uploaded_at)

  const [results, setResults] = useState<VesselResult[]>(() => resultCache.get(key) ?? [])
  const [isPending, setIsPending] = useState(false)
  const [progress, setProgress] = useState<OptimizeProgress | null>(null)
  const [hasRun, setHasRun] = useState(() => resultCache.has(key))

  useEffect(() => {
    const cached = resultCache.get(key)
    setResults(cached ?? [])
    setHasRun(cached !== undefined)
  }, [key])

  const handleOptimize = async () => {
    setIsPending(true)
    setProgress(null)
    setResults([])
    try {
      const data = await runOptimizeStream(
        { build_id: buildId, character_id: characterId },
        setProgress,
      )
      setResults(data)
      resultCache.set(key, data)
    } catch (err) {
      showErrorToast(err instanceof Error ? err.message : "Optimization failed")
    } finally {
      setIsPending(false)
      setProgress(null)
      setHasRun(true)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap gap-3 items-end">
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
          onClick={handleOptimize}
          disabled={!characterId || isPending}
        >
          {isPending ? "Optimizing…" : "Optimize"}
        </Button>
      </div>

      {isPending && (
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">
            {progress
              ? `Optimized ${progress.vessel} of ${progress.total} vessels (${progress.name})…`
              : "Starting…"}
          </p>
          <Progress value={progress ? (progress.vessel / progress.total) * 100 : 0} />
        </div>
      )}

      {chars.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No inventory found. <Link to="/upload" className="underline">Upload a save file</Link> first.
        </p>
      )}

      {!isPending && hasRun && results.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No matching relics found for this build. Check that your inventory has relics with the effects your build is looking for.
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
              effectMap={effectMap}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// --- Anonymous optimizer (inline mode, build from route) ---

interface SessionCharacter {
  slot_index: number
  name: string
  relics: Array<Record<string, unknown>>
}

function AnonOptimizeForm({ buildId }: { buildId: string }) {
  const { showErrorToast } = useCustomToast()
  const { getById } = useLocalBuilds()
  const { data: effectsData } = useSuspenseQuery({
    queryKey: ["game", "effects"],
    queryFn: () => GameService.getEffects(),
    staleTime: Infinity,
  })
  const effectMap = useMemo(() => buildEffectMap((effectsData ?? []) as unknown[]), [effectsData])
  const anonMeta = getAnonUploadMeta()

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

  const build = getById(buildId)
  const pinnedHandles = new Set<number>(build?.pinned_relics ?? [])
  const key = cacheKey("anon", buildId, build?.updated_at, selectedSlot, anonMeta?.uploaded_at)

  const [results, setResults] = useState<VesselResult[]>(() => resultCache.get(key) ?? [])
  const [isPending, setIsPending] = useState(false)
  const [progress, setProgress] = useState<OptimizeProgress | null>(null)
  const [hasRun, setHasRun] = useState(() => resultCache.has(key))

  useEffect(() => {
    const cached = resultCache.get(key)
    setResults(cached ?? [])
    setHasRun(cached !== undefined)
  }, [key])

  const handleOptimize = async () => {
    if (!build || !char) return

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

    setIsPending(true)
    setProgress(null)
    setResults([])
    try {
      const data = await runOptimizeStream(
        {
          build: {
            id: build.id,
            name: build.name,
            character: build.character,
            groups: build.groups,
            required_effects: build.required_effects,
            required_families: build.required_families,
            excluded_effects: build.excluded_effects,
            excluded_families: build.excluded_families,
            include_deep: build.include_deep,
            curse_max: build.curse_max,
            pinned_relics: build.pinned_relics ?? [],
          },
          relics,
        },
        setProgress,
      )
      setResults(data)
      resultCache.set(key, data)
    } catch (err) {
      showErrorToast(err instanceof Error ? err.message : "Optimization failed")
    } finally {
      setIsPending(false)
      setProgress(null)
      setHasRun(true)
    }
  }

  if (allChars.length === 0) {
    return (
      <p className="text-sm text-muted-foreground py-8">
        No inventory loaded.{" "}
        <Link to="/upload" className="underline">Upload a save file</Link> first.
      </p>
    )
  }

  if (!build) {
    return (
      <p className="text-sm text-muted-foreground py-8">
        Build not found. It may have been deleted or stored in a different browser.
      </p>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap gap-3 items-end">
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
          onClick={handleOptimize}
          disabled={isPending}
        >
          {isPending ? "Optimizing…" : "Optimize"}
        </Button>
      </div>

      {isPending && (
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">
            {progress
              ? `Optimized ${progress.vessel} of ${progress.total} vessels (${progress.name})…`
              : "Starting…"}
          </p>
          <Progress value={progress ? (progress.vessel / progress.total) * 100 : 0} />
        </div>
      )}

      <p className="text-xs text-muted-foreground border rounded-md px-3 py-2 bg-muted/40">
        Running in session mode with data from your uploaded save.{" "}
        <Link to="/login" search={{ redirect: `/builds/${buildId}/optimize` }} className="underline">
          Sign in
        </Link>{" "}
        to persist builds and inventory across devices.
      </p>

      {!isPending && hasRun && results.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No matching relics found for this build. Check that your inventory has relics with the effects your build is looking for.
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
              effectMap={effectMap}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// --- Page ---

function BuildOptimizePage() {
  const { buildId } = useParams({ from: "/_layout/builds/$buildId/optimize" })

  return (
    <Suspense fallback={<Skeleton className="h-32 w-full" />}>
      {isLoggedIn() ? <AuthOptimizeForm buildId={buildId} /> : <AnonOptimizeForm buildId={buildId} />}
    </Suspense>
  )
}
