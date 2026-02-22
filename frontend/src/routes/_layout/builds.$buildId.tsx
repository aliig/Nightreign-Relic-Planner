import { createFileRoute, useParams } from "@tanstack/react-router"
import { useMutation, useQueryClient, useSuspenseQuery } from "@tanstack/react-query"
import { Suspense, useCallback, useEffect, useRef, useState } from "react"
import { X, Search, Pencil } from "lucide-react"
import {
  DndContext,
  DragEndEvent,
  DragOverlay,
  DragStartEvent,
  PointerSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
} from "@dnd-kit/core"

import { BuildsService, GameService } from "@/client"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Separator } from "@/components/ui/separator"
import { cn } from "@/lib/utils"
import { isLoggedIn } from "@/hooks/useAuth"
import { useLocalBuilds } from "@/hooks/useLocalBuilds"
import useCustomToast from "@/hooks/useCustomToast"
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
type FamilyMeta = { name: string; member_names: string[]; member_ids: number[] }
type DragData =
  | { type: "effect"; effectId: number; sourceTier: string | null }
  | { type: "family"; familyName: string; sourceTier: string | null }

// --- DnD sub-components ---

function DraggableChip({
  dragId,
  name,
  tierKey,
  dragData,
  onRemove,
}: {
  dragId: string
  name: string
  tierKey: string
  dragData: DragData
  onRemove: () => void
}) {
  const { color } = TIER_DISPLAY[tierKey] ?? { color: "#888" }
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: dragId,
    data: dragData,
  })
  return (
    <span
      ref={setNodeRef}
      {...listeners}
      {...attributes}
      className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium border cursor-grab active:cursor-grabbing"
      style={{ borderColor: color, color, opacity: isDragging ? 0.3 : 1 }}
    >
      {name}
      <button
        type="button"
        onPointerDown={(e) => e.stopPropagation()}
        onClick={onRemove}
        className="hover:opacity-70 ml-0.5"
        aria-label={`Remove ${name}`}
      >
        <X className="h-3 w-3" />
      </button>
    </span>
  )
}

function DroppableTierZone({
  tierKey,
  children,
}: {
  tierKey: string
  children: React.ReactNode
}) {
  const { setNodeRef, isOver } = useDroppable({ id: `tier:${tierKey}` })
  const { color } = TIER_DISPLAY[tierKey]
  return (
    <div
      ref={setNodeRef}
      className={cn("rounded-lg border p-4 space-y-3 transition-colors", isOver && "bg-muted/20")}
      style={isOver ? { borderColor: color } : undefined}
    >
      {children}
    </div>
  )
}

function DraggableBrowserRow({
  dragId,
  data,
  onClick,
  children,
}: {
  dragId: string
  data: DragData
  onClick: () => void
  children: React.ReactNode
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: dragId,
    data,
  })
  return (
    <div
      ref={setNodeRef}
      {...listeners}
      {...attributes}
      onClick={onClick}
      className={cn(
        "flex items-center rounded px-2 py-1.5 hover:bg-muted/50 gap-2 cursor-grab active:cursor-grabbing select-none",
        isDragging && "opacity-40",
      )}
    >
      {children}
    </div>
  )
}

// --- Shared editor UI (works for both auth and anon) ---

interface EditorUIProps {
  name: string
  character: string
  tiers: Record<string, number[]>
  familyTiers: Record<string, string[]>
  includeDeep: boolean
  curseMax: number
  saving: boolean
  effects: EffectMeta[]
  families: FamilyMeta[]
  onTiersChange: (tiers: Record<string, number[]>) => void
  onFamilyTiersChange: (ft: Record<string, string[]>) => void
  onIncludeDeepChange: (v: boolean) => void
  onCurseMaxChange: (v: number) => void
  onRename: (newName: string) => void
}

function BuildEditorUI({
  name, character, tiers, familyTiers, includeDeep, curseMax,
  saving, effects, families,
  onTiersChange, onFamilyTiersChange, onIncludeDeepChange, onCurseMaxChange, onRename,
}: EditorUIProps) {
  const [effectSearch, setEffectSearch] = useState("")
  const [isRenaming, setIsRenaming] = useState(false)
  const [draftName, setDraftName] = useState(name)
  const [activeDragName, setActiveDragName] = useState<string | null>(null)

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  )

  function commitRename() {
    const trimmed = draftName.trim()
    if (trimmed && trimmed !== name) onRename(trimmed)
    setIsRenaming(false)
  }

  function cancelRename() {
    setDraftName(name)
    setIsRenaming(false)
  }

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

  const assignFamily = useCallback(
    (familyName: string, targetTier: string) => {
      const next = { ...familyTiers }
      for (const key of TIER_ORDER) {
        next[key] = (next[key] ?? []).filter((n) => n !== familyName)
      }
      next[targetTier] = [...(next[targetTier] ?? []), familyName]
      onFamilyTiersChange(next)
    },
    [familyTiers, onFamilyTiersChange],
  )

  const removeFamily = useCallback(
    (familyName: string, fromTier: string) => {
      onFamilyTiersChange({
        ...familyTiers,
        [fromTier]: (familyTiers[fromTier] ?? []).filter((n) => n !== familyName),
      })
    },
    [familyTiers, onFamilyTiersChange],
  )

  const effectMap = new Map(effects.map((e) => [e.id, e]))
  const assignedIds = new Set(Object.values(tiers).flat())
  const assignedFamilyNames = new Set(Object.values(familyTiers).flat())
  const filteredEffects = effects.filter(
    (e) =>
      !assignedIds.has(e.id) &&
      (effectSearch === "" || e.name.toLowerCase().includes(effectSearch.toLowerCase())),
  )
  const filteredFamilies = families.filter(
    (f) =>
      !assignedFamilyNames.has(f.name) &&
      (effectSearch === "" || f.name.toLowerCase().includes(effectSearch.toLowerCase())),
  )

  function handleDragStart(event: DragStartEvent) {
    const data = event.active.data.current as DragData
    if (data.type === "effect") {
      setActiveDragName(effectMap.get(data.effectId)?.name ?? "")
    } else {
      setActiveDragName(`${data.familyName} (group)`)
    }
  }

  function handleDragEnd(event: DragEndEvent) {
    setActiveDragName(null)
    const { active, over } = event
    if (!over) return
    const overId = over.id as string
    if (!overId.startsWith("tier:")) return
    const targetTier = overId.slice(5)
    const data = active.data.current as DragData
    if (data.type === "effect") {
      assignEffect(data.effectId, targetTier)
    } else {
      assignFamily(data.familyName, targetTier)
    }
  }

  return (
    <DndContext
      sensors={sensors}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
      onDragCancel={() => setActiveDragName(null)}
    >
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            {isRenaming ? (
              <Input
                autoFocus
                value={draftName}
                onChange={(e) => setDraftName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") { e.preventDefault(); commitRename() }
                  if (e.key === "Escape") cancelRename()
                }}
                onBlur={commitRename}
                className="text-2xl font-semibold h-auto py-0.5 w-64"
              />
            ) : (
              <div className="flex items-center gap-2 group">
                <h1 className="text-2xl font-semibold">{name}</h1>
                <button
                  type="button"
                  onClick={() => { setDraftName(name); setIsRenaming(true) }}
                  title="Rename build"
                  className="opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-foreground"
                >
                  <Pencil className="h-4 w-4" />
                </button>
              </div>
            )}
            <p className="text-muted-foreground text-sm mt-0.5">{character}</p>
          </div>
          {saving && <span className="text-sm text-muted-foreground">Saving…</span>}
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
              const tierFamilies = familyTiers[tierKey] ?? []
              const isEmpty = tierEffects.length === 0 && tierFamilies.length === 0

              return (
                <DroppableTierZone key={tierKey} tierKey={tierKey}>
                  <h3 className="text-sm font-semibold" style={{ color }}>{label}</h3>
                  {isEmpty ? (
                    <p className="text-xs text-muted-foreground italic">
                      Drop effects here, or click in the browser to add to Essential
                    </p>
                  ) : (
                    <div className="flex flex-wrap gap-2">
                      {tierFamilies.map((familyName) => (
                        <DraggableChip
                          key={`family:${familyName}`}
                          dragId={`family:${familyName}`}
                          name={`${familyName} (group)`}
                          tierKey={tierKey}
                          dragData={{ type: "family", familyName, sourceTier: tierKey }}
                          onRemove={() => removeFamily(familyName, tierKey)}
                        />
                      ))}
                      {tierEffects.map((e) => (
                        <DraggableChip
                          key={e.id}
                          dragId={`effect:${e.id}`}
                          name={e.name}
                          tierKey={tierKey}
                          dragData={{ type: "effect", effectId: e.id, sourceTier: tierKey }}
                          onRemove={() => removeEffect(e.id, tierKey)}
                        />
                      ))}
                    </div>
                  )}
                </DroppableTierZone>
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
              {/* Family groups */}
              {filteredFamilies.length > 0 && (
                <>
                  <p className="text-xs font-medium text-muted-foreground px-2 pt-1">Groups</p>
                  {filteredFamilies.map((family) => (
                    <DraggableBrowserRow
                      key={`family:${family.name}`}
                      dragId={`family:${family.name}`}
                      data={{ type: "family", familyName: family.name, sourceTier: null }}
                      onClick={() => assignFamily(family.name, "required")}
                    >
                      <span
                        className="text-sm truncate italic flex-1"
                        title={family.member_names.join(", ")}
                      >
                        {family.name}
                      </span>
                    </DraggableBrowserRow>
                  ))}
                  {filteredEffects.length > 0 && (
                    <p className="text-xs font-medium text-muted-foreground px-2 pt-2">Individual</p>
                  )}
                </>
              )}
              {/* Individual effects */}
              {filteredEffects.slice(0, 200).map((effect) => (
                <DraggableBrowserRow
                  key={effect.id}
                  dragId={`effect:${effect.id}`}
                  data={{ type: "effect", effectId: effect.id, sourceTier: null }}
                  onClick={() => assignEffect(effect.id, "required")}
                >
                  <span className="text-sm truncate flex-1" title={effect.name}>
                    {effect.name}
                    {effect.is_debuff && (
                      <span className="ml-1.5 text-xs text-muted-foreground">(debuff)</span>
                    )}
                  </span>
                </DraggableBrowserRow>
              ))}
              {filteredEffects.length === 0 && filteredFamilies.length === 0 && (
                <p className="text-xs text-muted-foreground text-center py-4">
                  No unassigned effects match.
                </p>
              )}
            </div>
          </div>
        </div>
      </div>

      <DragOverlay>
        {activeDragName && (
          <div className="rounded px-2.5 py-1 bg-background border shadow-lg text-sm font-medium select-none">
            {activeDragName}
          </div>
        )}
      </DragOverlay>
    </DndContext>
  )
}

// --- Authenticated editor (API-backed) ---

function AuthBuildEditorContent({ buildId }: { buildId: string }) {
  const { showErrorToast } = useCustomToast()
  const queryClient = useQueryClient()

  const { data: build } = useSuspenseQuery({
    queryKey: ["builds", buildId],
    queryFn: () => BuildsService.getBuild({ buildId }),
  })
  const { data: effectsData } = useSuspenseQuery({
    queryKey: ["game", "effects"],
    queryFn: () => GameService.getEffects(),
    staleTime: Infinity,
  })
  const { data: familiesData } = useSuspenseQuery({
    queryKey: ["game", "families"],
    queryFn: () => GameService.getFamilies(),
    staleTime: Infinity,
  })

  const effects = (effectsData ?? []) as EffectMeta[]
  const families = (familiesData ?? []) as FamilyMeta[]

  const [tiers, setTiers] = useState<Record<string, number[]>>(
    () => (build.tiers as Record<string, number[]>) ?? {},
  )
  const [familyTiers, setFamilyTiers] = useState<Record<string, string[]>>(
    () => (build.family_tiers as Record<string, string[]>) ?? {},
  )
  const [includeDeep, setIncludeDeep] = useState(build.include_deep)
  const [curseMax, setCurseMax] = useState(build.curse_max)

  // Refs always hold the latest values — safe to read inside the debounced timer
  const tiersRef = useRef(tiers)
  const familyTiersRef = useRef(familyTiers)
  const includeDeepRef = useRef(includeDeep)
  const curseMaxRef = useRef(curseMax)
  tiersRef.current = tiers
  familyTiersRef.current = familyTiers
  includeDeepRef.current = includeDeep
  curseMaxRef.current = curseMax

  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    setTiers((build.tiers as Record<string, number[]>) ?? {})
    setFamilyTiers((build.family_tiers as Record<string, string[]>) ?? {})
    setIncludeDeep(build.include_deep)
    setCurseMax(build.curse_max)
  }, [build])

  useEffect(() => {
    return () => { if (saveTimerRef.current) clearTimeout(saveTimerRef.current) }
  }, [])

  const saveMutation = useMutation({
    mutationFn: () =>
      BuildsService.updateBuild({
        buildId,
        requestBody: {
          tiers: tiersRef.current,
          family_tiers: familyTiersRef.current,
          include_deep: includeDeepRef.current,
          curse_max: curseMaxRef.current,
        },
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["builds"] }),
    onError: handleError.bind(showErrorToast),
  })

  const scheduleAutoSave = useCallback(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    saveTimerRef.current = setTimeout(() => saveMutation.mutate(), 800)
  }, [saveMutation])

  const renameMutation = useMutation({
    mutationFn: (name: string) =>
      BuildsService.updateBuild({ buildId, requestBody: { name } }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["builds"] }),
    onError: handleError.bind(showErrorToast),
  })

  return (
    <BuildEditorUI
      name={build.name}
      character={build.character}
      tiers={tiers}
      familyTiers={familyTiers}
      includeDeep={includeDeep}
      curseMax={curseMax}
      saving={saveMutation.isPending}
      effects={effects}
      families={families}
      onTiersChange={(t) => { setTiers(t); scheduleAutoSave() }}
      onFamilyTiersChange={(ft) => { setFamilyTiers(ft); scheduleAutoSave() }}
      onIncludeDeepChange={(v) => { setIncludeDeep(v); scheduleAutoSave() }}
      onCurseMaxChange={(v) => { setCurseMax(v); scheduleAutoSave() }}
      onRename={(name) => renameMutation.mutate(name)}
    />
  )
}

// --- Anonymous editor (localStorage-backed) ---

function LocalBuildEditorContent({ buildId }: { buildId: string }) {
  const { getById, update } = useLocalBuilds()

  const { data: effectsData } = useSuspenseQuery({
    queryKey: ["game", "effects"],
    queryFn: () => GameService.getEffects(),
    staleTime: Infinity,
  })
  const { data: familiesData } = useSuspenseQuery({
    queryKey: ["game", "families"],
    queryFn: () => GameService.getFamilies(),
    staleTime: Infinity,
  })

  const effects = (effectsData ?? []) as EffectMeta[]
  const families = (familiesData ?? []) as FamilyMeta[]
  const build = getById(buildId)

  const [tiers, setTiers] = useState<Record<string, number[]>>(
    () => build?.tiers ?? {},
  )
  const [familyTiers, setFamilyTiers] = useState<Record<string, string[]>>(
    () => (build?.family_tiers as Record<string, string[]>) ?? {},
  )
  const [includeDeep, setIncludeDeep] = useState(build?.include_deep ?? false)
  const [curseMax, setCurseMax] = useState(build?.curse_max ?? 0)

  const tiersRef = useRef(tiers)
  const familyTiersRef = useRef(familyTiers)
  const includeDeepRef = useRef(includeDeep)
  const curseMaxRef = useRef(curseMax)
  tiersRef.current = tiers
  familyTiersRef.current = familyTiers
  includeDeepRef.current = includeDeep
  curseMaxRef.current = curseMax

  const updateRef = useRef(update)
  updateRef.current = update

  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => { if (saveTimerRef.current) clearTimeout(saveTimerRef.current) }
  }, [])

  const scheduleAutoSave = useCallback(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    saveTimerRef.current = setTimeout(() => {
      updateRef.current(buildId, {
        tiers: tiersRef.current,
        family_tiers: familyTiersRef.current,
        include_deep: includeDeepRef.current,
        curse_max: curseMaxRef.current,
      })
    }, 400)
  }, [buildId])

  if (!build) {
    return (
      <p className="text-muted-foreground py-16 text-center">
        Build not found. It may have been deleted or stored in a different browser.
      </p>
    )
  }

  return (
    <BuildEditorUI
      name={build.name}
      character={build.character}
      tiers={tiers}
      familyTiers={familyTiers}
      includeDeep={includeDeep}
      curseMax={curseMax}
      saving={false}
      effects={effects}
      families={families}
      onTiersChange={(t) => { setTiers(t); scheduleAutoSave() }}
      onFamilyTiersChange={(ft) => { setFamilyTiers(ft); scheduleAutoSave() }}
      onIncludeDeepChange={(v) => { setIncludeDeep(v); scheduleAutoSave() }}
      onCurseMaxChange={(v) => { setCurseMax(v); scheduleAutoSave() }}
      onRename={(name) => updateRef.current(buildId, { name })}
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
