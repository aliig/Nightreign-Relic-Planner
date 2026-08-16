import {
  useQuery,
  useQueryClient,
  useSuspenseQuery,
} from "@tanstack/react-query"
import { createFileRoute, Link, useParams } from "@tanstack/react-router"
import {
  Fragment,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import type { BuildChange, VesselResult } from "@/client"
import {
  BuildsService,
  GameService,
  OptimizeService,
  SavesService,
} from "@/client"
import { ExportDebugButton } from "@/components/Debug/ExportDebugButton"
import {
  ChangeBanner,
  cacheKey,
  enteredKeys,
  MissingRequirementsSeparator,
  NoCoveringResultsBanner,
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
import { toInlineBuild, useLocalBuilds } from "@/hooks/useLocalBuilds"
import { getAnonUploadMeta, useSaveStatus } from "@/hooks/useSaveStatus"
import { stagedFields, stagedKey, usePendingSlot } from "@/lib/pendingChanges"
import { relicContentKey } from "@/lib/savedLoadoutMatch"

/** Amber note shown while displayed results predate the current staged diff.
 *  The auto re-run usually replaces them within moments; the banner also covers
 *  the failure case (the Optimize button is the manual retry). */
function StaleStagedBanner({ running }: { running: boolean }) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-400">
      <span>
        {running
          ? "Re-running with your staged relic changes…"
          : "These results don't include your staged relic changes — press Optimize to update."}
      </span>
    </div>
  )
}

export const Route = createFileRoute("/_layout/builds/$buildId/optimize")({
  component: BuildOptimizePage,
  head: () => ({
    meta: [{ title: "Optimize Build - Nightreign Relic Planner" }],
  }),
})

// --- Authenticated optimizer (DB-backed, build from route) ---

function AuthOptimizeForm({ buildId }: { buildId: string }) {
  const { showErrorToast } = useCustomToast()
  const queryClient = useQueryClient()
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

  const selectedBuild = buildRaw
  const profiles = profilesData?.data ?? []

  const [profileId, setProfileId] = useState(profiles[0]?.id ?? "")

  const { data: loadoutsData } = useQuery({
    queryKey: ["loadouts", profileId],
    queryFn: () => SavesService.getProfileLoadouts({ profileId }),
    enabled: !!profileId,
    staleTime: 5 * 60 * 1000,
  })

  // Resolves saved-loadout ga_handles to relic contents so the "Saved" badge
  // can also match content-equivalent arrangements (swapped same-color slots /
  // duplicate copies). Handle→content is fixed within one save, so the RAW
  // relic rows are the correct join source (same as the Loadouts page).
  const { data: profileRelicsData } = useQuery({
    queryKey: ["relics", profileId],
    queryFn: () => SavesService.getProfileRelics({ profileId }),
    enabled: !!profileId,
    staleTime: 5 * 60 * 1000,
  })
  const relicContentByHandle = useMemo(() => {
    const m = new Map<number, string>()
    for (const r of profileRelicsData?.data ?? []) {
      m.set(
        Number(r.ga_handle),
        relicContentKey({
          real_id: r.real_id,
          effects: [r.effect_1, r.effect_2, r.effect_3],
          curses: [r.curse_1, r.curse_2, r.curse_3],
        }),
      )
    }
    return m
  }, [profileRelicsData])

  const pinnedHandles = new Set<number>(selectedBuild?.pinned_relics ?? [])
  const hasRequirements =
    (selectedBuild?.required_effects?.length ?? 0) > 0 ||
    (selectedBuild?.required_families?.length ?? 0) > 0
  const slotIndex = profiles.find((p) => p.id === profileId)?.slot_index ?? null
  const pending = usePendingSlot(slotIndex)
  const sig = stagedKey(pending)
  // sig fully determines the staged wire fields, so it's the only real dep.
  // biome-ignore lint/correctness/useExhaustiveDependencies: sig covers pending's staged content
  const staged = useMemo(() => stagedFields(pending), [sig])

  const baseKey = cacheKey(
    "auth",
    buildId,
    selectedBuild?.updated_at,
    profileId,
    saveStatus?.uploaded_at,
  )

  const [results, setResults] = useState<VesselResult[]>(
    () => resultCache.get(baseKey)?.results ?? [],
  )
  // Staged signature the DISPLAYED results were computed with; a mismatch with
  // the live sig means "stale" — keep showing them while the re-run streams.
  const [resultsSig, setResultsSig] = useState<string | null>(
    () => resultCache.get(baseKey)?.sig ?? null,
  )
  const [isPending, setIsPending] = useState(false)
  const [progress, setProgress] = useState<OptimizeProgress | null>(null)
  const [hasRun, setHasRun] = useState(() => resultCache.has(baseKey))
  const [change, setChange] = useState<BuildChange | null>(null)
  const autoRunKeyRef = useRef<string | null>(null)

  const fresh = hasRun && resultsSig === sig

  // Load cached snapshot from server if fresh for the current staged state
  const { data: snapshot, isLoading: snapshotLoading } = useQuery({
    queryKey: ["snapshot", buildId, profileId, sig],
    queryFn: () =>
      OptimizeService.querySnapshot({
        requestBody: { build_id: buildId, profile_id: profileId, ...staged },
      }),
    enabled: !!profileId && !fresh,
    staleTime: Infinity,
  })

  // Populate results from snapshot when it arrives
  useEffect(() => {
    if (snapshot && !fresh) {
      setResults(snapshot.results)
      setResultsSig(sig)
      setChange(snapshot.last_change ?? null)
      resultCache.set(baseKey, { sig, results: snapshot.results })
      setHasRun(true)
    }
  }, [snapshot, baseKey, sig, fresh])

  useEffect(() => {
    const entry = resultCache.get(baseKey)
    setResults(entry?.results ?? [])
    setResultsSig(entry?.sig ?? null)
    setHasRun(entry !== undefined)
    setChange(null)
  }, [baseKey])

  const handleOptimize = useCallback(async () => {
    setIsPending(true)
    setProgress(null)
    setChange(null)
    try {
      const data = await runOptimizeStream(
        { build_id: buildId, profile_id: profileId, ...staged },
        setProgress,
        setChange,
      )
      setResults(data)
      setResultsSig(sig)
      resultCache.set(baseKey, { sig, results: data })
      queryClient.invalidateQueries({
        queryKey: ["snapshot", buildId, profileId],
      })
    } catch (err) {
      showErrorToast(err instanceof Error ? err.message : "Optimization failed")
    } finally {
      setIsPending(false)
      setProgress(null)
      setHasRun(true)
    }
  }, [buildId, profileId, staged, sig, baseKey, queryClient, showErrorToast])

  // Auto-optimize the VIEWED build: single-profile users on first view (as
  // before), plus whenever the staged diff makes the shown results stale —
  // the staged state is assumed live, so this build re-runs on its own.
  // Bulk "re-run every build" stays manual (too costly to fire per edit).
  useEffect(() => {
    if (!profileId || isPending || fresh) return
    if (snapshotLoading || snapshot) return
    if (sig === "" && profiles.length !== 1) return
    const autoKey = cacheKey(baseKey, sig)
    if (autoRunKeyRef.current === autoKey) return
    autoRunKeyRef.current = autoKey
    handleOptimize()
  }, [
    snapshotLoading,
    snapshot,
    handleOptimize,
    baseKey,
    sig,
    fresh,
    isPending,
    profileId,
    profiles.length,
  ])

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap gap-3 items-end">
        {profiles.length > 1 && (
          <div className="space-y-1.5">
            <span className="text-sm font-medium">Profile</span>
            <Select value={profileId} onValueChange={setProfileId}>
              <SelectTrigger className="w-56" aria-label="Profile">
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
        <ExportDebugButton
          mode="view"
          buildId={buildId}
          profileId={profileId}
          results={results}
        />
      </div>

      {isPending && (
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">
            {progress
              ? `Optimizing… ${progress.vessel} of ${progress.total} vessels`
              : "Starting…"}
          </p>
          <Progress
            value={progress ? (progress.vessel / progress.total) * 100 : 0}
          />
        </div>
      )}

      {snapshotLoading && !hasRun && !isPending && (
        <p className="text-sm text-muted-foreground animate-pulse">
          Loading cached results…
        </p>
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

      {!isPending && !snapshotLoading && hasRun && results.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No matching relics found for this build. Check that your inventory has
          relics with the effects your build is looking for.
        </p>
      )}

      {results.length > 0 && (
        <div className="space-y-3">
          {resultsSig !== null && resultsSig !== sig && (
            <StaleStagedBanner running={isPending} />
          )}
          <ChangeBanner change={change} effectMap={effectMap} />
          {hasRequirements &&
            results.every((r) => r.meets_requirements === false) && (
              <NoCoveringResultsBanner />
            )}
          <h2 className="text-lg font-medium">
            Top {results.length} vessel{results.length !== 1 ? "s" : ""}
          </h2>
          {results.map((vessel, index) => (
            <Fragment key={`${vessel.vessel_id}-${vessel.total_score}`}>
              {hasRequirements &&
                index > 0 &&
                vessel.meets_requirements === false &&
                results[index - 1].meets_requirements !== false && (
                  <MissingRequirementsSeparator />
                )}
              <VesselCard
                vessel={vessel}
                defaultExpanded={index === 0}
                highlighted={index === 0}
                hasRequirements={hasRequirements}
                pinnedHandles={pinnedHandles}
                effectMap={effectMap}
                enteredFingerprints={
                  index === 0 ? enteredKeys(change) : undefined
                }
                inventorySource={{
                  build_id: buildId,
                  profile_id: profileId,
                  ...staged,
                }}
                loadoutTarget={(() => {
                  const p = profiles.find((pr) => pr.id === profileId)
                  if (!p || !selectedBuild?.character) return undefined
                  return {
                    slotIndex: p.slot_index,
                    character: selectedBuild.character,
                    existing: (loadoutsData?.data ?? [])
                      .filter((l) => l.character === selectedBuild.character)
                      .map((l) => ({
                        index: l.index,
                        name: l.name,
                        vessel_id: l.vessel_id,
                        ga_handles: l.ga_handles ?? [],
                      })),
                    relicContentByHandle,
                  }
                })()}
              />
            </Fragment>
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
      const p = JSON.parse(sessionStorage.getItem("selectedProfile") ?? "null")
      return p?.slot_index ?? allProfiles[0]?.slot_index ?? null
    } catch {
      return allProfiles[0]?.slot_index ?? null
    }
  })()

  const [selectedSlot, setSelectedSlot] = useState<number | null>(defaultSlot)
  const profile =
    allProfiles.find((p) => p.slot_index === selectedSlot) ??
    allProfiles[0] ??
    null

  const handleProfileChange = (slotStr: string) => {
    const slot = Number(slotStr)
    setSelectedSlot(slot)
    const picked = allProfiles.find((p) => p.slot_index === slot)
    if (picked)
      sessionStorage.setItem("selectedProfile", JSON.stringify(picked))
  }

  const build = getById(buildId)
  const pinnedHandles = new Set<number>(build?.pinned_relics ?? [])
  const hasRequirements =
    (build?.required_effects?.length ?? 0) > 0 ||
    (build?.required_families?.length ?? 0) > 0
  const pending = usePendingSlot(profile?.slot_index ?? null)
  const sig = stagedKey(pending)
  const baseKey = cacheKey(
    "anon",
    buildId,
    build?.updated_at,
    selectedSlot,
    anonMeta?.uploaded_at,
  )

  // ParsedRelicData uses flat effect_1/2/3 fields; OwnedRelic expects arrays.
  // Lifted to a memo so the full optimize and per-slot strikes share identical
  // inventory inputs.  Inline mode has no server-side staged resolution, so the
  // staged diff (sells filtered out, mints appended under their synthetic
  // negative handles) is applied here — this IS the effective inventory.
  // biome-ignore lint/correctness/useExhaustiveDependencies: sig covers pending's staged content
  const relics = useMemo(() => {
    const base = (profile?.relics ?? []).map((r: any) => ({
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
    if (sig === "") return base
    const sold = new Set(pending.sells)
    return [
      ...base.filter((r: { ga_handle: number }) => !sold.has(r.ga_handle)),
      ...pending.mints.map((m) => ({
        ga_handle: m.handle,
        item_id: m.item_id,
        real_id: m.real_id,
        color: m.color,
        effects: m.effects,
        curses: m.curses,
        is_deep: m.isDeep,
        name: m.name,
        tier: m.tier,
      })),
    ]
  }, [profile, sig])

  // Handle→content join for the "Saved" badge's content-equivalent tier.
  // Built from the effective inventory above: staged-sold relics drop out
  // (loadouts holding them become unverifiable — never content-matched).
  const relicContentByHandle = useMemo(() => {
    const m = new Map<number, string>()
    for (const r of relics) m.set(Number(r.ga_handle), relicContentKey(r))
    return m
  }, [relics])

  const inlineBuild = useMemo(
    () => (build ? toInlineBuild(build) : null),
    [build],
  )

  const [results, setResults] = useState<VesselResult[]>(
    () => resultCache.get(baseKey)?.results ?? [],
  )
  const [resultsSig, setResultsSig] = useState<string | null>(
    () => resultCache.get(baseKey)?.sig ?? null,
  )
  const [isPending, setIsPending] = useState(false)
  const [progress, setProgress] = useState<OptimizeProgress | null>(null)
  const [hasRun, setHasRun] = useState(() => resultCache.has(baseKey))
  const [change, setChange] = useState<BuildChange | null>(null)
  const autoRunKeyRef = useRef<string | null>(null)

  const fresh = hasRun && resultsSig === sig

  useEffect(() => {
    const entry = resultCache.get(baseKey)
    setResults(entry?.results ?? [])
    setResultsSig(entry?.sig ?? null)
    setHasRun(entry !== undefined)
  }, [baseKey])

  const handleOptimize = useCallback(async () => {
    if (!build || !profile || !inlineBuild) return

    setIsPending(true)
    setProgress(null)
    setChange(null)
    try {
      const data = await runOptimizeStream(
        { build: inlineBuild, relics },
        setProgress,
        setChange,
      )
      setResults(data)
      setResultsSig(sig)
      resultCache.set(baseKey, { sig, results: data })
    } catch (err) {
      showErrorToast(err instanceof Error ? err.message : "Optimization failed")
    } finally {
      setIsPending(false)
      setProgress(null)
      setHasRun(true)
    }
  }, [build, profile, inlineBuild, relics, sig, baseKey, showErrorToast])

  // Auto-optimize: single-profile users on first view (as before), plus
  // whenever the staged diff makes the shown results stale (see auth form).
  useEffect(() => {
    if (!build || !profile || isPending || fresh) return
    if (sig === "" && allProfiles.length !== 1) return
    const autoKey = cacheKey(baseKey, sig)
    if (autoRunKeyRef.current === autoKey) return
    autoRunKeyRef.current = autoKey
    handleOptimize()
  }, [
    allProfiles.length,
    build,
    handleOptimize,
    baseKey,
    sig,
    fresh,
    isPending,
    profile,
  ])

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
            <span className="text-sm font-medium">Profile</span>
            <Select
              value={String(profile?.slot_index ?? "")}
              onValueChange={handleProfileChange}
            >
              <SelectTrigger className="w-56" aria-label="Profile">
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
              ? `Optimizing… ${progress.vessel} of ${progress.total} vessels`
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
          {resultsSig !== null && resultsSig !== sig && (
            <StaleStagedBanner running={isPending} />
          )}
          <ChangeBanner change={change} effectMap={effectMap} />
          {hasRequirements &&
            results.every((r) => r.meets_requirements === false) && (
              <NoCoveringResultsBanner />
            )}
          <h2 className="text-lg font-medium">
            Top {results.length} vessel{results.length !== 1 ? "s" : ""}
          </h2>
          {results.map((vessel, index) => (
            <Fragment key={`${vessel.vessel_id}-${vessel.total_score}`}>
              {hasRequirements &&
                index > 0 &&
                vessel.meets_requirements === false &&
                results[index - 1].meets_requirements !== false && (
                  <MissingRequirementsSeparator />
                )}
              <VesselCard
                vessel={vessel}
                defaultExpanded={index === 0}
                highlighted={index === 0}
                hasRequirements={hasRequirements}
                pinnedHandles={pinnedHandles}
                effectMap={effectMap}
                enteredFingerprints={
                  index === 0 ? enteredKeys(change) : undefined
                }
                inventorySource={
                  inlineBuild ? { build: inlineBuild, relics } : undefined
                }
                loadoutTarget={
                  profile?.slot_index != null && build?.character
                    ? {
                        slotIndex: Number(profile.slot_index),
                        character: build.character,
                        existing: (
                          ((profile as any)?.presets ?? []) as Array<{
                            index: number
                            name: string
                            character: string
                            vessel_id: number
                            ga_handles: number[]
                          }>
                        )
                          .filter((p) => p.character === build.character)
                          .map((p) => ({
                            index: p.index,
                            name: p.name,
                            vessel_id: p.vessel_id,
                            ga_handles: p.ga_handles ?? [],
                          })),
                        relicContentByHandle,
                      }
                    : undefined
                }
              />
            </Fragment>
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
