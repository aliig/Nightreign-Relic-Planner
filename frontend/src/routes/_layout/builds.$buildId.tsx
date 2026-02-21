import { createFileRoute, useParams } from "@tanstack/react-router"
import { useMutation, useQueryClient, useSuspenseQuery } from "@tanstack/react-query"
import { Suspense, useCallback, useEffect, useState } from "react"
import { X, Search } from "lucide-react"

import { BuildsService, GameService } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { Separator } from "@/components/ui/separator"
import { useCustomToast } from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/builds/$buildId")({
  component: BuildEditorPage,
  head: () => ({
    meta: [{ title: "Edit Build - Nightreign Relic Planner" }],
  }),
})

const TIER_DISPLAY: Record<string, { label: string; color: string }> = {
  required:     { label: "Essential",    color: "#FF4444" },
  preferred:    { label: "Preferred",    color: "#4488FF" },
  nice_to_have: { label: "Nice-to-Have", color: "#44BB88" },
  avoid:        { label: "Avoid",        color: "#888888" },
  blacklist:    { label: "Excluded",     color: "#FF8C00" },
}
const TIER_ORDER = ["required", "preferred", "nice_to_have", "avoid", "blacklist"]

type EffectMeta = { id: number; name: string; family?: string; is_debuff?: boolean }

function EffectChip({
  effectId,
  name,
  tierKey,
  onRemove,
}: {
  effectId: number
  name: string
  tierKey: string
  onRemove: () => void
}) {
  const { color } = TIER_DISPLAY[tierKey] ?? { color: "#888" }
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium border"
      style={{ borderColor: color, color }}
    >
      {name}
      <button
        type="button"
        onClick={onRemove}
        className="hover:opacity-70 ml-0.5"
        aria-label={`Remove ${name}`}
      >
        <X className="h-3 w-3" />
      </button>
    </span>
  )
}

function BuildEditorContent({ buildId }: { buildId: string }) {
  const queryClient = useQueryClient()
  const { showSuccessToast } = useCustomToast()

  const { data: build } = useSuspenseQuery({
    queryKey: ["builds", buildId],
    queryFn: () => BuildsService.getBuild({ buildId }),
  })

  const { data: effectsData } = useSuspenseQuery({
    queryKey: ["game", "effects"],
    queryFn: () => GameService.getEffects(),
    staleTime: Infinity,
  })

  const effects: EffectMeta[] = effectsData ?? []

  // Local state mirrors the build tiers so we can edit without constant re-fetches
  const [tiers, setTiers] = useState<Record<string, number[]>>(
    () => (build.tiers as Record<string, number[]>) ?? {},
  )
  const [includeDeep, setIncludeDeep] = useState(build.include_deep)
  const [curseMax, setCurseMax] = useState(build.curse_max)
  const [effectSearch, setEffectSearch] = useState("")
  const [dirty, setDirty] = useState(false)

  // Keep local state in sync if build reloads
  useEffect(() => {
    setTiers((build.tiers as Record<string, number[]>) ?? {})
    setIncludeDeep(build.include_deep)
    setCurseMax(build.curse_max)
    setDirty(false)
  }, [build])

  const saveMutation = useMutation({
    mutationFn: () =>
      BuildsService.updateBuild({
        buildId,
        requestBody: { tiers, include_deep: includeDeep, curse_max: curseMax },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["builds"] })
      showSuccessToast("Build saved.")
      setDirty(false)
    },
    onError: handleError,
  })

  const assignEffect = useCallback(
    (effectId: number, targetTier: string) => {
      setTiers((prev) => {
        const next = { ...prev }
        // Remove from any existing tier first
        for (const key of TIER_ORDER) {
          next[key] = (next[key] ?? []).filter((id) => id !== effectId)
        }
        next[targetTier] = [...(next[targetTier] ?? []), effectId]
        return next
      })
      setDirty(true)
    },
    [],
  )

  const removeEffect = useCallback((effectId: number, fromTier: string) => {
    setTiers((prev) => ({
      ...prev,
      [fromTier]: (prev[fromTier] ?? []).filter((id) => id !== effectId),
    }))
    setDirty(true)
  }, [])

  // Build a map for quick name lookup
  const effectMap = new Map(effects.map((e) => [e.id, e]))

  // Which effects are already assigned somewhere
  const assignedIds = new Set(Object.values(tiers).flat())

  // Filtered available effects
  const filteredEffects = effects.filter(
    (e) =>
      !assignedIds.has(e.id) &&
      (effectSearch === "" ||
        e.name.toLowerCase().includes(effectSearch.toLowerCase())),
  )

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-semibold">{build.name}</h1>
          <p className="text-muted-foreground text-sm mt-0.5">{build.character}</p>
        </div>
        <Button
          onClick={() => saveMutation.mutate()}
          disabled={!dirty || saveMutation.isPending}
        >
          {saveMutation.isPending ? "Saving…" : dirty ? "Save Changes" : "Saved"}
        </Button>
      </div>

      {/* Settings */}
      <div className="flex flex-wrap items-center gap-6 p-4 rounded-lg border bg-muted/30">
        <div className="flex items-center gap-2">
          <Switch
            id="include-deep"
            checked={includeDeep}
            onCheckedChange={(v) => { setIncludeDeep(v); setDirty(true) }}
          />
          <Label htmlFor="include-deep">Include deep relics</Label>
        </div>
        <div className="flex items-center gap-2">
          <Label htmlFor="curse-max">Max curse stacks</Label>
          <Input
            id="curse-max"
            type="number"
            min={0}
            max={3}
            value={curseMax}
            onChange={(e) => { setCurseMax(Number(e.target.value)); setDirty(true) }}
            className="w-16"
          />
        </div>
      </div>

      <div className="grid lg:grid-cols-[1fr_320px] gap-6">
        {/* Tier columns */}
        <div className="space-y-4">
          {TIER_ORDER.map((tierKey) => {
            const { label, color } = TIER_DISPLAY[tierKey]
            const tierEffects = (tiers[tierKey] ?? [])
              .map((id) => effectMap.get(id))
              .filter(Boolean) as EffectMeta[]

            return (
              <div key={tierKey} className="rounded-lg border p-4 space-y-3">
                <h3 className="text-sm font-semibold" style={{ color }}>
                  {label}
                </h3>
                {tierEffects.length === 0 ? (
                  <p className="text-xs text-muted-foreground italic">
                    No effects assigned. Pick from the browser →
                  </p>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {tierEffects.map((e) => (
                      <EffectChip
                        key={e.id}
                        effectId={e.id}
                        name={e.name}
                        tierKey={tierKey}
                        onRemove={() => removeEffect(e.id, tierKey)}
                      />
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </div>

        {/* Effect browser */}
        <div className="rounded-lg border p-4 space-y-3 self-start sticky top-20">
          <h3 className="text-sm font-semibold">Effect Browser</h3>
          <div className="relative">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search effects…"
              value={effectSearch}
              onChange={(e) => setEffectSearch(e.target.value)}
              className="pl-8"
            />
          </div>
          <Separator />
          <div className="space-y-1 max-h-[480px] overflow-y-auto pr-1">
            {filteredEffects.slice(0, 200).map((effect) => (
              <div
                key={effect.id}
                className="group flex items-center justify-between rounded px-2 py-1.5 hover:bg-muted/50 gap-2"
              >
                <span className="text-sm truncate" title={effect.name}>
                  {effect.name}
                  {effect.is_debuff && (
                    <span className="ml-1.5 text-xs text-muted-foreground">(debuff)</span>
                  )}
                </span>
                <div className="hidden group-hover:flex gap-1 shrink-0">
                  {TIER_ORDER.slice(0, 3).map((tk) => (
                    <button
                      key={tk}
                      type="button"
                      title={`Add to ${TIER_DISPLAY[tk].label}`}
                      onClick={() => assignEffect(effect.id, tk)}
                      className="w-2 h-2 rounded-full hover:scale-125 transition-transform"
                      style={{ background: TIER_DISPLAY[tk].color }}
                    />
                  ))}
                </div>
              </div>
            ))}
            {filteredEffects.length === 0 && (
              <p className="text-xs text-muted-foreground text-center py-4">
                No unassigned effects match.
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function BuildEditorPage() {
  const { buildId } = useParams({ from: "/_layout/builds/$buildId" })

  return (
    <Suspense fallback={<Skeleton className="h-64 w-full" />}>
      <BuildEditorContent buildId={buildId} />
    </Suspense>
  )
}
