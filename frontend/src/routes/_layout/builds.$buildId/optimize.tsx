import { useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute, Link, useParams } from "@tanstack/react-router"
import { Suspense, useEffect, useMemo, useRef, useState } from "react"
import type { VesselResult } from "@/client"
import { BuildsService, GameService, SavesService } from "@/client"
import {
  cacheKey,
  type OptimizeProgress,
  resultCache,
  runOptimizeStream,
  VesselCard,
} from "@/components/OptimizeResults"
import { buildEffectMap } from "@/components/RelicDisplay"
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
import useCustomToast from "@/hooks/useCustomToast"
import { useLocalBuilds } from "@/hooks/useLocalBuilds"
import { getAnonUploadMeta, useSaveStatus } from "@/hooks/useSaveStatus"

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
  const { data: profilesData } = useSuspenseQuery({
    queryKey: ["profiles"],
    queryFn: () => SavesService.listProfiles(),
  })
  const { data: effectsData } = useSuspenseQuery({
    queryKey: ["game", "effects"],
    queryFn: () => GameService.getEffects(),
    staleTime: Infinity,
  })
  const { status: saveStatus } = useSaveStatus()
  const effectMap = useMemo(
    () => buildEffectMap((effectsData ?? []) as unknown[]),
    [effectsData],
  )

  const selectedBuild = buildRaw as any
  const profiles = profilesData?.data ?? []

  const [profileId, setProfileId] = useState(profiles[0]?.id ?? "")

  const pinnedHandles = new Set<number>(selectedBuild?.pinned_relics ?? [])
  const key = cacheKey(
    "auth",
    buildId,
    selectedBuild?.updated_at,
    profileId,
    saveStatus?.uploaded_at,
  )

  const [results, setResults] = useState<VesselResult[]>(
    () => resultCache.get(key) ?? [],
  )
  const [isPending, setIsPending] = useState(false)
  const [progress, setProgress] = useState<OptimizeProgress | null>(null)
  const [hasRun, setHasRun] = useState(() => resultCache.has(key))
  const autoOptimizeRef = useRef(false)

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
        { build_id: buildId, profile_id: profileId },
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

  // Auto-optimize when there's only one profile and no cached results
  useEffect(() => {
    if (profiles.length === 1 && profileId && !resultCache.has(key) && !autoOptimizeRef.current) {
      autoOptimizeRef.current = true
      handleOptimize()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap gap-3 items-end">
        {profiles.length > 1 && (
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Profile</label>
            <Select value={profileId} onValueChange={setProfileId}>
              <SelectTrigger className="w-56">
                <SelectValue placeholder="Select profile" />
              </SelectTrigger>
              <SelectContent>
                {profiles.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
        <Button onClick={handleOptimize} disabled={!profileId || isPending}>
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
          <Progress
            value={progress ? (progress.vessel / progress.total) * 100 : 0}
          />
        </div>
      )}

      {profiles.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No inventory found.{" "}
          <Link to="/upload" className="underline">
            Upload a save file
          </Link>{" "}
          first.
        </p>
      )}

      {!isPending && hasRun && results.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No matching relics found for this build. Check that your inventory has
          relics with the effects your build is looking for.
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

interface SessionProfile {
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
  const effectMap = useMemo(
    () => buildEffectMap((effectsData ?? []) as unknown[]),
    [effectsData],
  )
  const anonMeta = getAnonUploadMeta()

  const allProfiles: SessionProfile[] = JSON.parse(
    sessionStorage.getItem("parsedProfiles") ?? "[]",
  )

  const defaultSlot = (() => {
    try {
      const p = JSON.parse(
        sessionStorage.getItem("selectedProfile") ?? "null",
      )
      return p?.slot_index ?? allProfiles[0]?.slot_index ?? null
    } catch {
      return allProfiles[0]?.slot_index ?? null
    }
  })()

  const [selectedSlot, setSelectedSlot] = useState<number | null>(defaultSlot)
  const profile =
    allProfiles.find((p) => p.slot_index === selectedSlot) ?? allProfiles[0] ?? null

  const handleProfileChange = (slotStr: string) => {
    const slot = Number(slotStr)
    setSelectedSlot(slot)
    const picked = allProfiles.find((p) => p.slot_index === slot)
    if (picked)
      sessionStorage.setItem("selectedProfile", JSON.stringify(picked))
  }

  const build = getById(buildId)
  const pinnedHandles = new Set<number>(build?.pinned_relics ?? [])
  const key = cacheKey(
    "anon",
    buildId,
    build?.updated_at,
    selectedSlot,
    anonMeta?.uploaded_at,
  )

  const [results, setResults] = useState<VesselResult[]>(
    () => resultCache.get(key) ?? [],
  )
  const [isPending, setIsPending] = useState(false)
  const [progress, setProgress] = useState<OptimizeProgress | null>(null)
  const [hasRun, setHasRun] = useState(() => resultCache.has(key))
  const autoOptimizeRef = useRef(false)

  useEffect(() => {
    const cached = resultCache.get(key)
    setResults(cached ?? [])
    setHasRun(cached !== undefined)
  }, [key])

  const handleOptimize = async () => {
    if (!build || !profile) return

    // ParsedRelicData uses flat effect_1/2/3 fields; OwnedRelic expects arrays
    const relics = profile.relics.map((r: any) => ({
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
            required_effects: [],
            required_families: [],
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

  // Auto-optimize when there's only one profile and no cached results
  useEffect(() => {
    if (allProfiles.length === 1 && build && profile && !resultCache.has(key) && !autoOptimizeRef.current) {
      autoOptimizeRef.current = true
      handleOptimize()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  if (allProfiles.length === 0) {
    return (
      <p className="text-sm text-muted-foreground py-8">
        No inventory loaded.{" "}
        <Link to="/upload" className="underline">
          Upload a save file
        </Link>{" "}
        first.
      </p>
    )
  }

  if (!build) {
    return (
      <p className="text-sm text-muted-foreground py-8">
        Build not found. It may have been deleted or stored in a different
        browser.
      </p>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap gap-3 items-end">
        {allProfiles.length > 1 && (
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Profile</label>
            <Select
              value={String(profile?.slot_index ?? "")}
              onValueChange={handleProfileChange}
            >
              <SelectTrigger className="w-56">
                <SelectValue placeholder="Select profile" />
              </SelectTrigger>
              <SelectContent>
                {allProfiles.map((p) => (
                  <SelectItem key={p.slot_index} value={String(p.slot_index)}>
                    {p.name} (Slot {p.slot_index})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
        <Button onClick={handleOptimize} disabled={isPending}>
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
          <Progress
            value={progress ? (progress.vessel / progress.total) * 100 : 0}
          />
        </div>
      )}

      <p className="text-xs text-muted-foreground border rounded-md px-3 py-2 bg-muted/40">
        Running in session mode with data from your uploaded save.{" "}
        <Link
          to="/login"
          search={{ redirect: `/builds/${buildId}/optimize` }}
          className="underline"
        >
          Sign in
        </Link>{" "}
        to persist builds and inventory across devices.
      </p>

      {!isPending && hasRun && results.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No matching relics found for this build. Check that your inventory has
          relics with the effects your build is looking for.
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
      {isLoggedIn() ? (
        <AuthOptimizeForm buildId={buildId} />
      ) : (
        <AnonOptimizeForm buildId={buildId} />
      )}
    </Suspense>
  )
}
