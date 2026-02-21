import { createFileRoute, useParams } from "@tanstack/react-router"
import { useMutation, useQueryClient, useSuspenseQuery } from "@tanstack/react-query"
import { Suspense, useCallback, useEffect, useState } from "react"
import { X, Search } from "lucide-react"

import { BuildsService, GameService } from "@/client"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Separator } from "@/components/ui/separator"
import useCustomToast from "@/hooks/useCustomToast"
import { isLoggedIn } from "@/hooks/useAuth"
import { useLocalBuilds } from "@/hooks/useLocalBuilds"
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
  name,
  tierKey,
  onRemove,
}: {
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

// --- Shared editor UI (works for both auth and anon) ---

interface EditorUIProps {
  name: string
  character: string
  tiers: Record<string, number[]>
  includeDeep: boolean
  curseMax: number
  dirty: boolean
  saving: boolean
  effects: EffectMeta[]
  onTiersChange: (tiers: Record<string, number[]>) => void
  onIncludeDeepChange: (v: boolean) => void
  onCurseMaxChange: (v: number) => void
  onSave: () => void
}

function BuildEditorUI({
  name, character, tiers, includeDeep, curseMax,
  dirty, saving, effects,
  onTiersChange, onIncludeDeepChange, onCurseMaxChange, onSave,
}: EditorUIProps) {
  const [effectSearch, setEffectSearch] = useState("")

  const assignEffect = useCallback(
    (effectId: number, targetTier: string) => {
      const next = { ...tiers }
      for (const key of TIER_ORDER) {
        next[key] = (next[key] ?? []).filter((id) => id !== effectId)
      }
      next[targetTier] = [...(next[targetTier] ?? []), effectId]
      onTiersChange(next)
    },
    [tiers, onTiersChange],
  )

  const removeEffect = useCallback(
    (effectId: number, fromTier: string) => {
      onTiersChange({
        ...tiers,
        [fromTier]: (tiers[fromTier] ?? []).filter((id) => id !== effectId),
      })
    },
    [tiers, onTiersChange],
  )

  const effectMap = new Map(effects.map((e) => [e.id, e]))
  const assignedIds = new Set(Object.values(tiers).flat())
  const filteredEffects = effects.filter(
    (e) =>
      !assignedIds.has(e.id) &&
      (effectSearch === "" || e.name.toLowerCase().includes(effectSearch.toLowerCase())),
  )

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-semibold">{name}</h1>
          <p className="text-muted-foreground text-sm mt-0.5">{character}</p>
        </div>
        <Button onClick={onSave} disabled={!dirty || saving}>
          {saving ? "Saving…" : dirty ? "Save Changes" : "Saved"}
        </Button>
      </div>

      {/* Settings */}
      <div className="flex flex-wrap items-center gap-6 p-4 rounded-lg border bg-muted/30">
        <div className="flex items-center gap-2">
          <Checkbox
            id="include-deep"
            checked={includeDeep}
            onCheckedChange={(v: boolean) => onIncludeDeepChange(v)}
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
            onChange={(e) => onCurseMaxChange(Number(e.target.value))}
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
                <h3 className="text-sm font-semibold" style={{ color }}>{label}</h3>
                {tierEffects.length === 0 ? (
                  <p className="text-xs text-muted-foreground italic">
                    No effects assigned. Pick from the browser →
                  </p>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {tierEffects.map((e) => (
                      <EffectChip
                        key={e.id}
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

// --- Authenticated editor (API-backed) ---

function AuthBuildEditorContent({ buildId }: { buildId: string }) {
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

  const effects = (effectsData ?? []) as EffectMeta[]
  const [tiers, setTiers] = useState<Record<string, number[]>>(
    () => (build.tiers as Record<string, number[]>) ?? {},
  )
  const [includeDeep, setIncludeDeep] = useState(build.include_deep)
  const [curseMax, setCurseMax] = useState(build.curse_max)
  const [dirty, setDirty] = useState(false)

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

  return (
    <BuildEditorUI
      name={build.name}
      character={build.character}
      tiers={tiers}
      includeDeep={includeDeep}
      curseMax={curseMax}
      dirty={dirty}
      saving={saveMutation.isPending}
      effects={effects}
      onTiersChange={(t) => { setTiers(t); setDirty(true) }}
      onIncludeDeepChange={(v) => { setIncludeDeep(v); setDirty(true) }}
      onCurseMaxChange={(v) => { setCurseMax(v); setDirty(true) }}
      onSave={() => saveMutation.mutate()}
    />
  )
}

// --- Anonymous editor (localStorage-backed) ---

function LocalBuildEditorContent({ buildId }: { buildId: string }) {
  const { getById, update } = useLocalBuilds()
  const { showSuccessToast } = useCustomToast()

  const { data: effectsData } = useSuspenseQuery({
    queryKey: ["game", "effects"],
    queryFn: () => GameService.getEffects(),
    staleTime: Infinity,
  })

  const effects = (effectsData ?? []) as EffectMeta[]
  const build = getById(buildId)

  const [tiers, setTiers] = useState<Record<string, number[]>>(
    () => build?.tiers ?? {},
  )
  const [includeDeep, setIncludeDeep] = useState(build?.include_deep ?? false)
  const [curseMax, setCurseMax] = useState(build?.curse_max ?? 0)
  const [dirty, setDirty] = useState(false)

  if (!build) {
    return (
      <p className="text-muted-foreground py-16 text-center">
        Build not found. It may have been deleted or stored in a different browser.
      </p>
    )
  }

  function handleSave() {
    update(buildId, { tiers, include_deep: includeDeep, curse_max: curseMax })
    showSuccessToast("Build saved.")
    setDirty(false)
  }

  return (
    <BuildEditorUI
      name={build.name}
      character={build.character}
      tiers={tiers}
      includeDeep={includeDeep}
      curseMax={curseMax}
      dirty={dirty}
      saving={false}
      effects={effects}
      onTiersChange={(t) => { setTiers(t); setDirty(true) }}
      onIncludeDeepChange={(v) => { setIncludeDeep(v); setDirty(true) }}
      onCurseMaxChange={(v) => { setCurseMax(v); setDirty(true) }}
      onSave={handleSave}
    />
  )
}

// --- Page ---

function BuildEditorPage() {
  const { buildId } = useParams({ from: "/_layout/builds/$buildId" })

  return (
    <Suspense fallback={<Skeleton className="h-64 w-full" />}>
      {isLoggedIn()
        ? <AuthBuildEditorContent buildId={buildId} />
        : <LocalBuildEditorContent buildId={buildId} />
      }
    </Suspense>
  )
}
