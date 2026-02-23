import { createFileRoute, useParams } from "@tanstack/react-router"
import { useMutation, useQuery, useQueryClient, useSuspenseQuery } from "@tanstack/react-query"
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react"
import { X, Search, Pin } from "lucide-react"
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

import { BuildsService, GameService, SavesService, type ParsedRelicData } from "@/client"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Separator } from "@/components/ui/separator"
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip"
import { buildEffectMap, EffectList, EMPTY_EFFECT } from "@/components/RelicDisplay"
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

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type EffectMeta = { id: number; name: string; family?: string; is_debuff?: boolean }
type FamilyMeta = { name: string; member_names: string[]; member_ids: number[] }
type TierConfig = {
  key: string
  display_name: string
  color: string
  weight: number
  scored: boolean
  is_exclusion: boolean
}
type RelicForPicker = {
  ga_handle: number
  name: string
  color: string
  is_deep: boolean
}
type DragData =
  | { type: "effect"; effectId: number; sourceTier: string | null }
  | { type: "family"; familyName: string; sourceTier: string | null }

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const COLOR_HEX: Record<string, string> = {
  Red: "#FF4444", Blue: "#4488FF", Yellow: "#B8860B", Green: "#44BB44", White: "#AAAAAA",
}

// ---------------------------------------------------------------------------
// DnD sub-components
// ---------------------------------------------------------------------------

function DraggableChip({
  dragId, name, color, dragData, onRemove,
}: {
  dragId: string; name: string; color: string
  dragData: DragData; onRemove: () => void
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: dragId, data: dragData,
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
  tierKey, color, children,
}: {
  tierKey: string; color: string; children: React.ReactNode
}) {
  const { setNodeRef, isOver } = useDroppable({ id: `tier:${tierKey}` })
  return (
    <div
      ref={setNodeRef}
      className={cn("rounded-md border border-border/60 p-4 space-y-3 transition-colors", isOver && "bg-muted/20")}
      style={isOver ? { borderColor: color } : undefined}
    >
      {children}
    </div>
  )
}

function DraggableBrowserRow({
  dragId, data, onClick, children,
}: {
  dragId: string; data: DragData; onClick: () => void; children: React.ReactNode
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: dragId, data,
  })
  return (
    <div
      ref={setNodeRef}
      {...listeners}
      {...attributes}
      onClick={onClick}
      className={cn(
        "flex items-center rounded px-2 py-1.5 hover:bg-gradient-to-r hover:from-accent/40 hover:to-transparent transition-colors gap-2 cursor-grab active:cursor-grabbing select-none",
        isDragging && "opacity-40",
      )}
    >
      {children}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Pinned relic picker dialog
// ---------------------------------------------------------------------------

function PinnedRelicPickerContent({
  characterId, onSelect, effects,
}: {
  characterId: string; onSelect: (relic: RelicForPicker) => void; effects: EffectMeta[]
}) {
  const { data } = useSuspenseQuery({
    queryKey: ["relics", characterId],
    queryFn: () => SavesService.getCharacterRelics({ characterId }),
    staleTime: 5 * 60 * 1000,
  })
  const [search, setSearch] = useState("")
  const relics = (data.data ?? []).filter((r) =>
    !search || r.name.toLowerCase().includes(search.toLowerCase()),
  )
  const effectMap = useMemo(() => buildEffectMap(effects), [effects])

  return (
    <>
      <div className="relative mb-2">
        <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
        <Input
          autoFocus
          placeholder="Search relics…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="pl-8"
        />
      </div>
      <div className="space-y-1 max-h-64 overflow-y-auto">
        {relics.map((r) => {
          const effectIds = [r.effect_1, r.effect_2, r.effect_3]
          const curseIds = [r.curse_1, r.curse_2, r.curse_3]
          const hasEffects = effectIds.some((id) => id !== 0 && id !== EMPTY_EFFECT)
          return (
            <Tooltip key={r.id}>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  onClick={() =>
                    onSelect({ ga_handle: r.ga_handle, name: r.name, color: r.color, is_deep: r.is_deep })
                  }
                  className="w-full text-left rounded px-2 py-1.5 hover:bg-gradient-to-r hover:from-accent/40 hover:to-transparent transition-colors flex items-center gap-2 text-sm"
                >
                  <span
                    className="w-2 h-2 rounded-full shrink-0"
                    style={{ background: COLOR_HEX[r.color] ?? "#888" }}
                  />
                  <span className="truncate" style={{ color: COLOR_HEX[r.color] ?? undefined }}>
                    {r.name}
                  </span>
                  <span className="ml-auto text-xs text-muted-foreground shrink-0">
                    {r.tier} {r.is_deep ? "· Deep" : ""}
                  </span>
                </button>
              </TooltipTrigger>
              {hasEffects && (
                <TooltipContent side="right" className="max-w-64 space-y-1.5">
                  <EffectList effectIds={effectIds} isCurse={false} effectMap={effectMap} />
                  <EffectList effectIds={curseIds} isCurse={true} effectMap={effectMap} />
                </TooltipContent>
              )}
            </Tooltip>
          )
        })}
        {relics.length === 0 && (
          <p className="text-xs text-muted-foreground text-center py-4">No relics match.</p>
        )}
      </div>
    </>
  )
}

function AuthPinnedRelicDialog({
  pinnedHandles, onAdd, disabled, effects,
}: {
  pinnedHandles: number[]
  onAdd: (relic: RelicForPicker) => void
  disabled: boolean
  effects: EffectMeta[]
}) {
  const [open, setOpen] = useState(false)
  const [charId, setCharId] = useState<string | null>(null)

  const { data: charsData } = useQuery({
    queryKey: ["characters"],
    queryFn: () => SavesService.listCharacters(),
    staleTime: 5 * 60 * 1000,
  })
  const chars = charsData?.data ?? []
  const selectedCharId = charId ?? chars[0]?.id ?? null

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button type="button" size="sm" variant="outline" disabled={disabled}>
          <Pin className="h-3.5 w-3.5 mr-1" /> Pin Relic
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Pin a Relic</DialogTitle>
        </DialogHeader>
        {chars.length > 1 && (
          <Select value={selectedCharId ?? ""} onValueChange={setCharId}>
            <SelectTrigger className="w-full">
              <SelectValue placeholder="Select character" />
            </SelectTrigger>
            <SelectContent>
              {chars.map((c) => (
                <SelectItem key={c.id} value={c.id}>
                  {c.name} (Slot {c.slot_index})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        {selectedCharId ? (
          <Suspense fallback={<Skeleton className="h-40 w-full" />}>
            <PinnedRelicPickerContent
              characterId={selectedCharId}
              effects={effects}
              onSelect={(relic) => {
                if (!pinnedHandles.includes(relic.ga_handle)) {
                  onAdd(relic)
                }
                setOpen(false)
              }}
            />
          </Suspense>
        ) : (
          <p className="text-sm text-muted-foreground py-4 text-center">
            No characters found. Upload a save file first.
          </p>
        )}
      </DialogContent>
    </Dialog>
  )
}

function AnonPinnedRelicDialog({
  pinnedHandles, onAdd, disabled, effects,
}: {
  pinnedHandles: number[]
  onAdd: (relic: RelicForPicker) => void
  disabled: boolean
  effects: EffectMeta[]
}) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState("")

  const raw = sessionStorage.getItem("selectedCharacter")
  const char = raw ? JSON.parse(raw) : null
  const relics: ParsedRelicData[] = (char?.relics ?? []).filter(
    (r: ParsedRelicData) =>
      !search || r.name.toLowerCase().includes(search.toLowerCase()),
  )
  const effectMap = useMemo(() => buildEffectMap(effects), [effects])

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button type="button" size="sm" variant="outline" disabled={disabled}>
          <Pin className="h-3.5 w-3.5 mr-1" /> Pin Relic
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Pin a Relic</DialogTitle>
        </DialogHeader>
        <div className="relative mb-2">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            autoFocus
            placeholder="Search relics…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8"
          />
        </div>
        <div className="space-y-1 max-h-64 overflow-y-auto">
          {relics.map((r) => {
            const effectIds = [r.effect_1, r.effect_2, r.effect_3]
            const curseIds = [r.curse_1, r.curse_2, r.curse_3]
            const hasEffects = effectIds.some((id) => id !== 0 && id !== EMPTY_EFFECT)
            return (
              <Tooltip key={r.ga_handle}>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    onClick={() => {
                      if (!pinnedHandles.includes(r.ga_handle)) onAdd(r)
                      setOpen(false)
                    }}
                    className="w-full text-left rounded px-2 py-1.5 hover:bg-gradient-to-r hover:from-accent/40 hover:to-transparent transition-colors flex items-center gap-2 text-sm"
                  >
                    <span
                      className="w-2 h-2 rounded-full shrink-0"
                      style={{ background: COLOR_HEX[r.color] ?? "#888" }}
                    />
                    <span className="truncate" style={{ color: COLOR_HEX[r.color] ?? undefined }}>
                      {r.name}
                    </span>
                    <span className="ml-auto text-xs text-muted-foreground shrink-0">
                      {r.is_deep ? "Deep" : "Standard"}
                    </span>
                  </button>
                </TooltipTrigger>
                {hasEffects && (
                  <TooltipContent side="right" className="max-w-64 space-y-1.5">
                    <EffectList effectIds={effectIds} isCurse={false} effectMap={effectMap} />
                    <EffectList effectIds={curseIds} isCurse={true} effectMap={effectMap} />
                  </TooltipContent>
                )}
              </Tooltip>
            )
          })}
          {relics.length === 0 && (
            <p className="text-xs text-muted-foreground text-center py-4">No relics match.</p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// Shared editor UI
// ---------------------------------------------------------------------------

interface EditorUIProps {
  name: string
  character: string
  tiers: Record<string, number[]>
  familyTiers: Record<string, string[]>
  includeDeep: boolean
  curseMax: number
  tierConfigs: TierConfig[]
  tierWeights: Record<string, number> | null | undefined
  pinnedRelics: number[]
  pinnedRelicMeta: Map<number, RelicForPicker>
  saving: boolean
  effects: EffectMeta[]
  families: FamilyMeta[]
  isAuth: boolean
  onTiersChange: (tiers: Record<string, number[]>) => void
  onFamilyTiersChange: (ft: Record<string, string[]>) => void
  onIncludeDeepChange: (v: boolean) => void
  onCurseMaxChange: (v: number) => void
  onTierWeightsChange: (w: Record<string, number> | null) => void
  onPinnedRelicsChange: (handles: number[], meta: Map<number, RelicForPicker>) => void
  onRename: (newName: string) => void
}

function BuildEditorUI({
  name, character, tiers, familyTiers, includeDeep, curseMax,
  tierConfigs, tierWeights, pinnedRelics, pinnedRelicMeta,
  saving, effects, families, isAuth,
  onTiersChange, onFamilyTiersChange, onIncludeDeepChange, onCurseMaxChange,
  onTierWeightsChange, onPinnedRelicsChange, onRename,
}: EditorUIProps) {
  const [effectSearch, setEffectSearch] = useState("")
  const [draftName, setDraftName] = useState(name)
  const [activeDragName, setActiveDragName] = useState<string | null>(null)

  // Keep draft name in sync with prop (e.g., after save roundtrip)
  useEffect(() => { setDraftName(name) }, [name])

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  )

  // Compute effective weights (server defaults merged with build overrides)
  const effectiveWeights = useMemo(() => {
    const defaults = Object.fromEntries(tierConfigs.map((t) => [t.key, t.weight]))
    return tierWeights ? { ...defaults, ...tierWeights } : defaults
  }, [tierConfigs, tierWeights])

  // Sort tiers: non-blacklist by weight desc, blacklist always last
  const sortedTierKeys = useMemo(() => {
    const nonBlacklist = tierConfigs
      .filter((t) => !t.is_exclusion)
      .map((t) => t.key)
      .sort((a, b) => (effectiveWeights[b] ?? 0) - (effectiveWeights[a] ?? 0))
    const blacklistKeys = tierConfigs.filter((t) => t.is_exclusion).map((t) => t.key)
    return [...nonBlacklist, ...blacklistKeys]
  }, [tierConfigs, effectiveWeights])

  // Build display info per tier (fixed label from config + color)
  const tierDisplay = useMemo(() => {
    return Object.fromEntries(
      tierConfigs.map((t) => [
        t.key,
        {
          label: t.display_name,
          color: t.color,
          weight: effectiveWeights[t.key] ?? t.weight,
          scored: t.scored,
          is_exclusion: t.is_exclusion,
        },
      ]),
    )
  }, [tierConfigs, effectiveWeights])

  const allTierKeys = tierConfigs.map((t) => t.key)

  function commitRename() {
    const trimmed = draftName.trim()
    if (trimmed && trimmed !== name) {
      onRename(trimmed)
    } else {
      setDraftName(name)
    }
  }

  const assignEffect = useCallback(
    (effectId: number, targetTier: string) => {
      const next = { ...tiers }
      for (const key of allTierKeys) {
        next[key] = (next[key] ?? []).filter((id) => id !== effectId)
      }
      next[targetTier] = [...(next[targetTier] ?? []), effectId]
      onTiersChange(next)
    },
    [tiers, allTierKeys, onTiersChange],
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
      for (const key of allTierKeys) {
        next[key] = (next[key] ?? []).filter((n) => n !== familyName)
      }
      next[targetTier] = [...(next[targetTier] ?? []), familyName]
      onFamilyTiersChange(next)
    },
    [familyTiers, allTierKeys, onFamilyTiersChange],
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

  function handleWeightChange(tierKey: string, value: number) {
    const defaults = Object.fromEntries(tierConfigs.map((t) => [t.key, t.weight]))
    const next = { ...defaults, ...(tierWeights ?? {}), [tierKey]: value }
    // If all weights match defaults, clear the override (null = use defaults)
    const isAllDefault = tierConfigs.every((t) => next[t.key] === t.weight)
    onTierWeightsChange(isAllDefault ? null : next)
  }

  const maxPins = includeDeep ? 6 : 3
  const atPinLimit = pinnedRelics.length >= maxPins

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
            <input
              value={draftName}
              onChange={(e) => setDraftName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") { e.preventDefault(); e.currentTarget.blur() }
                if (e.key === "Escape") { setDraftName(name); e.currentTarget.blur() }
              }}
              onBlur={commitRename}
              className="text-2xl font-semibold bg-transparent border-b border-transparent hover:border-muted-foreground/30 focus:border-primary focus:outline-none focus:ring-0 py-0.5 w-64 transition-colors"
            />
            <p className="text-muted-foreground text-sm mt-0.5">{character}</p>
          </div>
          {saving && <span className="text-sm text-muted-foreground">Saving…</span>}
        </div>

        {/* Settings */}
        <div className="p-4 rounded-md border border-border/60 bg-card/60 backdrop-blur-md space-y-4">
          <div className="flex flex-wrap items-center gap-6">
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
                min={1}
                max={3}
                value={curseMax}
                onChange={(e) => onCurseMaxChange(Number(e.target.value))}
                className="w-16"
              />
            </div>
          </div>

          {/* Pinned relics */}
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-medium">Pinned Relics</span>
              <span className="text-xs text-muted-foreground">
                {pinnedRelics.length}/{maxPins}
              </span>
              {isAuth ? (
                <AuthPinnedRelicDialog
                  pinnedHandles={pinnedRelics}
                  effects={effects}
                  onAdd={(relic) => {
                    const nextMeta = new Map(pinnedRelicMeta)
                    nextMeta.set(relic.ga_handle, relic)
                    onPinnedRelicsChange([...pinnedRelics, relic.ga_handle], nextMeta)
                  }}
                  disabled={atPinLimit}
                />
              ) : (
                <AnonPinnedRelicDialog
                  pinnedHandles={pinnedRelics}
                  effects={effects}
                  onAdd={(relic) => {
                    const nextMeta = new Map(pinnedRelicMeta)
                    nextMeta.set(relic.ga_handle, relic)
                    onPinnedRelicsChange([...pinnedRelics, relic.ga_handle], nextMeta)
                  }}
                  disabled={atPinLimit}
                />
              )}
            </div>
            {pinnedRelics.length > 0 && (
              <div className="flex flex-wrap gap-2 mt-2">
                {pinnedRelics.map((handle) => {
                  const relic = pinnedRelicMeta.get(handle)
                  const color = relic ? (COLOR_HEX[relic.color] ?? "#888") : "#888"
                  const label = relic ? relic.name : `#${handle}`
                  return (
                    <span
                      key={handle}
                      className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium border"
                      style={{ borderColor: color, color }}
                    >
                      {label}
                      <button
                        type="button"
                        onClick={() => {
                          const nextMeta = new Map(pinnedRelicMeta)
                          nextMeta.delete(handle)
                          onPinnedRelicsChange(
                            pinnedRelics.filter((h) => h !== handle),
                            nextMeta,
                          )
                        }}
                        className="hover:opacity-70 ml-0.5"
                        aria-label={`Unpin ${label}`}
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </span>
                  )
                })}
              </div>
            )}
          </div>
        </div>

        <div className="grid lg:grid-cols-[1fr_320px] gap-6">
          {/* Tier columns */}
          <div className="space-y-4">
            {tierWeights && (
              <button
                type="button"
                onClick={() => onTierWeightsChange(null)}
                className="text-xs text-muted-foreground underline"
              >
                Reset weights to defaults
              </button>
            )}
            {sortedTierKeys.map((tierKey) => {
              const { label, color, is_exclusion } = tierDisplay[tierKey] ?? { label: tierKey, color: "#888", is_exclusion: false }
              const tierEffects = (tiers[tierKey] ?? [])
                .map((id) => effectMap.get(id))
                .filter(Boolean) as EffectMeta[]
              const tierFamilies = familyTiers[tierKey] ?? []
              const isEmpty = tierEffects.length === 0 && tierFamilies.length === 0

              return (
                <div key={tierKey}>
                  <div className="flex items-center justify-between mb-1">
                    <h3 className="text-sm font-semibold" style={{ color }}>{label}</h3>
                    {!is_exclusion && (
                      <input
                        type="number"
                        min={-100}
                        max={100}
                        value={effectiveWeights[tierKey] ?? 0}
                        onChange={(e) => handleWeightChange(tierKey, Number(e.target.value))}
                        className="w-16 text-xs text-right text-muted-foreground bg-transparent border-b border-transparent hover:border-muted-foreground/30 focus:border-primary focus:outline-none focus:ring-0 py-0.5 transition-colors [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                        title="Tier weight"
                      />
                    )}
                  </div>
                  <DroppableTierZone tierKey={tierKey} color={color}>
                    {isEmpty ? (
                      <p className="text-xs text-muted-foreground italic">
                        Drop effects here, or click in the browser to add
                      </p>
                    ) : (
                      <div className="flex flex-wrap gap-2">
                        {tierFamilies.map((familyName) => (
                          <DraggableChip
                            key={`family:${familyName}`}
                            dragId={`family:${familyName}`}
                            name={`${familyName} (group)`}
                            color={color}
                            dragData={{ type: "family", familyName, sourceTier: tierKey }}
                            onRemove={() => removeFamily(familyName, tierKey)}
                          />
                        ))}
                        {tierEffects.map((e) => (
                          <DraggableChip
                            key={e.id}
                            dragId={`effect:${e.id}`}
                            name={e.name}
                            color={color}
                            dragData={{ type: "effect", effectId: e.id, sourceTier: tierKey }}
                            onRemove={() => removeEffect(e.id, tierKey)}
                          />
                        ))}
                      </div>
                    )}
                  </DroppableTierZone>
                </div>
              )
            })}
          </div>

          {/* Effect browser */}
          <div className="rounded-md border border-border/60 bg-card/60 backdrop-blur-md p-4 space-y-3 self-start sticky top-20">
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
                  <p className="text-xs font-medium text-muted-foreground px-2 pt-1">Groups (Weighted Effect Scaling)</p>
                  {filteredFamilies.map((family) => (
                    <DraggableBrowserRow
                      key={`family:${family.name}`}
                      dragId={`family:${family.name}`}
                      data={{ type: "family", familyName: family.name, sourceTier: null }}
                      onClick={() => assignFamily(family.name, sortedTierKeys[0] ?? "required")}
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
                  onClick={() => assignEffect(effect.id, sortedTierKeys[0] ?? "required")}
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

// ---------------------------------------------------------------------------
// Authenticated editor (API-backed)
// ---------------------------------------------------------------------------

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
  const { data: tiersData } = useSuspenseQuery({
    queryKey: ["game", "tiers"],
    queryFn: () => GameService.getTiers(),
    staleTime: Infinity,
  })

  const effects = (effectsData ?? []) as EffectMeta[]
  const families = (familiesData ?? []) as FamilyMeta[]
  const tierConfigs = (tiersData ?? []) as TierConfig[]

  const [tiers, setTiers] = useState<Record<string, number[]>>(
    () => (build.tiers as Record<string, number[]>) ?? {},
  )
  const [familyTiers, setFamilyTiers] = useState<Record<string, string[]>>(
    () => (build.family_tiers as Record<string, string[]>) ?? {},
  )
  const [includeDeep, setIncludeDeep] = useState(build.include_deep)
  const [curseMax, setCurseMax] = useState(build.curse_max)
  const [tierWeights, setTierWeights] = useState<Record<string, number> | null | undefined>(
    build.tier_weights ?? null,
  )
  const [pinnedRelics, setPinnedRelics] = useState<number[]>(build.pinned_relics ?? [])
  const [pinnedRelicMeta, setPinnedRelicMeta] = useState<Map<number, RelicForPicker>>(new Map())

  const tiersRef = useRef(tiers)
  const familyTiersRef = useRef(familyTiers)
  const includeDeepRef = useRef(includeDeep)
  const curseMaxRef = useRef(curseMax)
  const tierWeightsRef = useRef(tierWeights)
  const pinnedRelicsRef = useRef(pinnedRelics)
  tiersRef.current = tiers
  familyTiersRef.current = familyTiers
  includeDeepRef.current = includeDeep
  curseMaxRef.current = curseMax
  tierWeightsRef.current = tierWeights
  pinnedRelicsRef.current = pinnedRelics

  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // On mount: populate pinnedRelicMeta for handles already stored in the build
  // so chips show names instead of raw IDs after a page reload.
  useEffect(() => {
    const pinned = build.pinned_relics ?? []
    if (pinned.length === 0) return
    const pinnedSet = new Set(pinned)

    async function populateMeta() {
      const charsResponse = await queryClient.fetchQuery({
        queryKey: ["characters"],
        queryFn: () => SavesService.listCharacters(),
        staleTime: 5 * 60 * 1000,
      })
      const chars = charsResponse?.data ?? []
      const meta = new Map<number, RelicForPicker>()

      for (const char of chars) {
        if (pinnedSet.size === 0) break
        const relicsResponse = await queryClient.fetchQuery({
          queryKey: ["relics", char.id],
          queryFn: () => SavesService.getCharacterRelics({ characterId: char.id }),
          staleTime: 5 * 60 * 1000,
        })
        for (const r of relicsResponse?.data ?? []) {
          if (pinnedSet.has(r.ga_handle)) {
            meta.set(r.ga_handle, {
              ga_handle: r.ga_handle,
              name: r.name,
              color: r.color,
              is_deep: r.is_deep,
            })
            pinnedSet.delete(r.ga_handle)
          }
        }
      }

      if (meta.size > 0) setPinnedRelicMeta(meta)
    }

    populateMeta()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setTiers((build.tiers as Record<string, number[]>) ?? {})
    setFamilyTiers((build.family_tiers as Record<string, string[]>) ?? {})
    setIncludeDeep(build.include_deep)
    setCurseMax(build.curse_max)
    setTierWeights(build.tier_weights ?? null)
    setPinnedRelics(build.pinned_relics ?? [])
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
          tier_weights: tierWeightsRef.current ?? null,
          pinned_relics: pinnedRelicsRef.current,
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
      tierConfigs={tierConfigs}
      tierWeights={tierWeights}
      pinnedRelics={pinnedRelics}
      pinnedRelicMeta={pinnedRelicMeta}
      saving={saveMutation.isPending}
      effects={effects}
      families={families}
      isAuth={true}
      onTiersChange={(t) => { setTiers(t); scheduleAutoSave() }}
      onFamilyTiersChange={(ft) => { setFamilyTiers(ft); scheduleAutoSave() }}
      onIncludeDeepChange={(v) => { setIncludeDeep(v); scheduleAutoSave() }}
      onCurseMaxChange={(v) => { setCurseMax(v); scheduleAutoSave() }}
      onTierWeightsChange={(w) => { setTierWeights(w); scheduleAutoSave() }}
      onPinnedRelicsChange={(handles, meta) => {
        setPinnedRelics(handles)
        setPinnedRelicMeta(meta)
        scheduleAutoSave()
      }}
      onRename={(name) => renameMutation.mutate(name)}
    />
  )
}

// ---------------------------------------------------------------------------
// Anonymous editor (localStorage-backed)
// ---------------------------------------------------------------------------

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
  const { data: tiersData } = useSuspenseQuery({
    queryKey: ["game", "tiers"],
    queryFn: () => GameService.getTiers(),
    staleTime: Infinity,
  })

  const effects = (effectsData ?? []) as EffectMeta[]
  const families = (familiesData ?? []) as FamilyMeta[]
  const tierConfigs = (tiersData ?? []) as TierConfig[]
  const build = getById(buildId)

  const [tiers, setTiers] = useState<Record<string, number[]>>(
    () => build?.tiers ?? {},
  )
  const [familyTiers, setFamilyTiers] = useState<Record<string, string[]>>(
    () => (build?.family_tiers as Record<string, string[]>) ?? {},
  )
  const [includeDeep, setIncludeDeep] = useState(build?.include_deep ?? false)
  const [curseMax, setCurseMax] = useState(build?.curse_max ?? 1)
  const [tierWeights, setTierWeights] = useState<Record<string, number> | null | undefined>(
    build?.tier_weights ?? null,
  )
  const [pinnedRelics, setPinnedRelics] = useState<number[]>(build?.pinned_relics ?? [])
  const [pinnedRelicMeta, setPinnedRelicMeta] = useState<Map<number, RelicForPicker>>(() => {
    // Pre-populate from sessionStorage so chips show names after a page reload.
    const raw = sessionStorage.getItem("selectedCharacter")
    const char = raw ? JSON.parse(raw) : null
    const handles = new Set(build?.pinned_relics ?? [])
    const map = new Map<number, RelicForPicker>()
    for (const r of (char?.relics ?? []) as RelicForPicker[]) {
      if (handles.has(r.ga_handle)) {
        map.set(r.ga_handle, { ga_handle: r.ga_handle, name: r.name, color: r.color, is_deep: r.is_deep })
      }
    }
    return map
  })

  const tiersRef = useRef(tiers)
  const familyTiersRef = useRef(familyTiers)
  const includeDeepRef = useRef(includeDeep)
  const curseMaxRef = useRef(curseMax)
  const tierWeightsRef = useRef(tierWeights)
  const pinnedRelicsRef = useRef(pinnedRelics)
  tiersRef.current = tiers
  familyTiersRef.current = familyTiers
  includeDeepRef.current = includeDeep
  curseMaxRef.current = curseMax
  tierWeightsRef.current = tierWeights
  pinnedRelicsRef.current = pinnedRelics

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
        tier_weights: tierWeightsRef.current ?? null,
        pinned_relics: pinnedRelicsRef.current,
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
      tierConfigs={tierConfigs}
      tierWeights={tierWeights}
      pinnedRelics={pinnedRelics}
      pinnedRelicMeta={pinnedRelicMeta}
      saving={false}
      effects={effects}
      families={families}
      isAuth={false}
      onTiersChange={(t) => { setTiers(t); scheduleAutoSave() }}
      onFamilyTiersChange={(ft) => { setFamilyTiers(ft); scheduleAutoSave() }}
      onIncludeDeepChange={(v) => { setIncludeDeep(v); scheduleAutoSave() }}
      onCurseMaxChange={(v) => { setCurseMax(v); scheduleAutoSave() }}
      onTierWeightsChange={(w) => { setTierWeights(w); scheduleAutoSave() }}
      onPinnedRelicsChange={(handles, meta) => {
        setPinnedRelics(handles)
        setPinnedRelicMeta(meta)
        scheduleAutoSave()
      }}
      onRename={(name) => updateRef.current(buildId, { name })}
    />
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

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
