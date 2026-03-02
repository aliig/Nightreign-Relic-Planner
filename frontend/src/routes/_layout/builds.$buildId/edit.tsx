import { createFileRoute, useParams } from "@tanstack/react-router"
import { useMutation, useQuery, useQueryClient, useSuspenseQuery } from "@tanstack/react-query"
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react"
import { X, Search, Pin, Plus, Trash2 } from "lucide-react"
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
import { buildEffectMap, DEEP_COLOR, EffectList, EMPTY_EFFECT } from "@/components/RelicDisplay"
import { cn } from "@/lib/utils"
import { isLoggedIn } from "@/hooks/useAuth"
import { useLocalBuilds, type WeightGroup } from "@/hooks/useLocalBuilds"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/builds/$buildId/edit")({
  component: BuildEditorPage,
  head: () => ({
    meta: [{ title: "Edit Build - Nightreign Relic Planner" }],
  }),
})

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type EffectMeta = { id: number; name: string; family?: string; is_debuff?: boolean; source?: string | null }
type FamilyMeta = { name: string; member_names: string[]; member_ids: number[] }
type RelicForPicker = {
  ga_handle: number
  name: string
  color: string
  is_deep: boolean
}
type DragData =
  | { type: "effect"; effectId: number; sourceZone: string | null }
  | { type: "family"; familyName: string; sourceZone: string | null }
type StackingCategory = {
  compatibility_id: number
  label: string
  effect_ids: number[]
  member_names: string[]
}

// Shape of the build data returned by the API (new schema).
// Cast API response to this type until the SDK is regenerated.
type BuildApiData = {
  name: string
  character: string
  groups: WeightGroup[]
  required_effects: number[]
  required_families: string[]
  excluded_effects: number[]
  excluded_families: string[]
  include_deep: boolean
  curse_max: number
  pinned_relics: number[]
  excluded_stacking_categories: number[]
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const COLOR_HEX: Record<string, string> = {
  Red: "#FF4444", Blue: "#4488FF", Yellow: "#B8860B", Green: "#44BB44", White: "#AAAAAA",
}

const DEFAULT_GROUPS: WeightGroup[] = [
  { weight: 50, effects: [], families: [] },
  { weight: 25, effects: [], families: [] },
  { weight: 10, effects: [], families: [] },
  { weight: -20, effects: [], families: [] },
]

const REQUIRED_COLOR = "#FF8C00"
const EXCLUDED_COLOR = "#CC4444"

/** Weight input that buffers keystrokes locally and only commits on blur/Enter,
 *  so the sort order doesn't jump around while the user is typing. */
function WeightInput({ value, onChange, className, title }: {
  value: number
  onChange: (v: number) => void
  className?: string
  title?: string
}) {
  const [draft, setDraft] = useState(String(value))
  const inputRef = useRef<HTMLInputElement>(null)

  // Sync draft when external value changes (e.g. undo, load) but not while focused
  useEffect(() => {
    if (document.activeElement !== inputRef.current) {
      setDraft(String(value))
    }
  }, [value])

  function commit() {
    const n = Number(draft)
    if (!Number.isNaN(n) && n !== value) {
      onChange(Math.max(-100, Math.min(100, n)))
    } else {
      setDraft(String(value)) // revert invalid input
    }
  }

  return (
    <input
      ref={inputRef}
      type="number"
      min={-100}
      max={100}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => { if (e.key === "Enter") { e.currentTarget.blur() } }}
      className={className}
      title={title}
    />
  )
}

function getLabelForWeight(weight: number): { label: string; color: string } {
  if (weight >= 75) return { label: "Essential", color: "#FF4444" }
  if (weight >= 35) return { label: "Preferred", color: "#4488FF" }
  if (weight >= 15) return { label: "Nice to Have", color: "#44BB88" }
  if (weight >= 1) return { label: "Bonus", color: "#9966CC" }
  if (weight < 0) return { label: "Avoid", color: "#888888" }
  return { label: "Neutral", color: "#AAAAAA" }
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
      className="inline-flex items-center gap-1 rounded-none px-2.5 py-0.5 text-xs font-medium border cursor-grab active:cursor-grabbing tracking-wide"
      style={{ borderColor: color, color, opacity: isDragging ? 0.3 : 1, backgroundColor: `${color}10`, boxShadow: `inset 0 0 10px ${color}05` }}
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

function DroppableZone({
  zoneId, color, children, className,
}: {
  zoneId: string; color: string; children: React.ReactNode; className?: string
}) {
  const { setNodeRef, isOver } = useDroppable({ id: zoneId })
  return (
    <div
      ref={setNodeRef}
      className={cn("rounded-none border border-border/60 p-3 transition-all duration-300 shadow-[inset_0_4px_12px_rgba(0,0,0,0.3)] bg-black/20", isOver && "bg-muted/40 shadow-[inset_0_4px_20px_rgba(0,0,0,0.5)]", className)}
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
  groups: WeightGroup[]
  requiredEffects: number[]
  requiredFamilies: string[]
  excludedEffects: number[]
  excludedFamilies: string[]
  includeDeep: boolean
  curseMax: number
  pinnedRelics: number[]
  pinnedRelicMeta: Map<number, RelicForPicker>
  excludedStackingCategories: number[]
  stackingCategories: StackingCategory[]
  saving: boolean
  effects: EffectMeta[]
  families: FamilyMeta[]
  isAuth: boolean
  onGroupsChange: (groups: WeightGroup[]) => void
  onRequiredEffectsChange: (ids: number[]) => void
  onRequiredFamiliesChange: (names: string[]) => void
  onExcludedEffectsChange: (ids: number[]) => void
  onExcludedFamiliesChange: (names: string[]) => void
  onIncludeDeepChange: (v: boolean) => void
  onCurseMaxChange: (v: number) => void
  onPinnedRelicsChange: (handles: number[], meta: Map<number, RelicForPicker>) => void
  onExcludedStackingCategoriesChange: (ids: number[]) => void
  onRename: (newName: string) => void
}

function BuildEditorUI({
  name, character, groups, requiredEffects, requiredFamilies,
  excludedEffects, excludedFamilies, includeDeep, curseMax,
  pinnedRelics, pinnedRelicMeta, excludedStackingCategories, stackingCategories,
  saving, effects, families, isAuth,
  onGroupsChange, onRequiredEffectsChange, onRequiredFamiliesChange,
  onExcludedEffectsChange, onExcludedFamiliesChange,
  onIncludeDeepChange, onCurseMaxChange, onPinnedRelicsChange,
  onExcludedStackingCategoriesChange, onRename,
}: EditorUIProps) {
  const [effectSearch, setEffectSearch] = useState("")
  const [draftName, setDraftName] = useState(name)
  const [activeDragName, setActiveDragName] = useState<string | null>(null)

  useEffect(() => { setDraftName(name) }, [name])

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  )

  const effectMap = new Map(effects.map((e) => [e.id, e]))

  // Groups sorted by weight descending for display; zone IDs use original array index
  const sortedGroupIndices = useMemo(
    () => groups.map((_, i) => i).sort((a, b) => groups[b].weight - groups[a].weight),
    [groups],
  )

  // Groups sectioned by derived label so same-label groups share one section header
  const groupSections = useMemo(() => {
    const map = new Map<string, { label: string; color: string; indices: number[] }>()
    const ordered: { label: string; color: string; indices: number[] }[] = []
    for (const idx of sortedGroupIndices) {
      const { label, color } = getLabelForWeight(groups[idx].weight)
      if (!map.has(label)) {
        const s = { label, color, indices: [idx] }
        map.set(label, s)
        ordered.push(s)
      } else {
        map.get(label)!.indices.push(idx)
      }
    }
    return ordered
  }, [sortedGroupIndices, groups])

  // All effect IDs and family names currently assigned to any zone
  const assignedEffectIds = useMemo(() => {
    const ids = new Set<number>()
    for (const id of requiredEffects) ids.add(id)
    for (const id of excludedEffects) ids.add(id)
    for (const g of groups) for (const id of g.effects) ids.add(id)
    return ids
  }, [requiredEffects, excludedEffects, groups])

  const assignedFamilyNames = useMemo(() => {
    const names = new Set<string>()
    for (const n of requiredFamilies) names.add(n)
    for (const n of excludedFamilies) names.add(n)
    for (const g of groups) for (const n of g.families) names.add(n)
    return names
  }, [requiredFamilies, excludedFamilies, groups])

  const filteredEffects = effects.filter(
    (e) =>
      !assignedEffectIds.has(e.id) &&
      (effectSearch === "" || e.name.toLowerCase().includes(effectSearch.toLowerCase())),
  )
  const filteredFamilies = families.filter(
    (f) =>
      !assignedFamilyNames.has(f.name) &&
      (effectSearch === "" || f.name.toLowerCase().includes(effectSearch.toLowerCase())),
  )

  function commitRename() {
    const trimmed = draftName.trim()
    if (trimmed && trimmed !== name) {
      onRename(trimmed)
    } else {
      setDraftName(name)
    }
  }

  // Move effect to a zone (removing it from all other zones first)
  const assignEffect = useCallback(
    (effectId: number, targetZone: string) => {
      const newGroups = groups.map((g) => ({ ...g, effects: g.effects.filter((id) => id !== effectId) }))
      const newRequired = requiredEffects.filter((id) => id !== effectId)
      const newExcluded = excludedEffects.filter((id) => id !== effectId)

      if (targetZone === "zone:required") {
        onGroupsChange(newGroups)
        onRequiredEffectsChange([...newRequired, effectId])
        onExcludedEffectsChange(newExcluded)
      } else if (targetZone === "zone:excluded") {
        onGroupsChange(newGroups)
        onRequiredEffectsChange(newRequired)
        onExcludedEffectsChange([...newExcluded, effectId])
      } else if (targetZone.startsWith("zone:group:")) {
        const idx = parseInt(targetZone.slice("zone:group:".length))
        onGroupsChange(newGroups.map((g, i) => i === idx ? { ...g, effects: [...g.effects, effectId] } : g))
        onRequiredEffectsChange(newRequired)
        onExcludedEffectsChange(newExcluded)
      }
    },
    [groups, requiredEffects, excludedEffects, onGroupsChange, onRequiredEffectsChange, onExcludedEffectsChange],
  )

  // Move family to a zone (removing it from all other zones first)
  const assignFamily = useCallback(
    (familyName: string, targetZone: string) => {
      const newGroups = groups.map((g) => ({ ...g, families: g.families.filter((n) => n !== familyName) }))
      const newRequired = requiredFamilies.filter((n) => n !== familyName)
      const newExcluded = excludedFamilies.filter((n) => n !== familyName)

      if (targetZone === "zone:required") {
        onGroupsChange(newGroups)
        onRequiredFamiliesChange([...newRequired, familyName])
        onExcludedFamiliesChange(newExcluded)
      } else if (targetZone === "zone:excluded") {
        onGroupsChange(newGroups)
        onRequiredFamiliesChange(newRequired)
        onExcludedFamiliesChange([...newExcluded, familyName])
      } else if (targetZone.startsWith("zone:group:")) {
        const idx = parseInt(targetZone.slice("zone:group:".length))
        onGroupsChange(newGroups.map((g, i) => i === idx ? { ...g, families: [...g.families, familyName] } : g))
        onRequiredFamiliesChange(newRequired)
        onExcludedFamiliesChange(newExcluded)
      }
    },
    [groups, requiredFamilies, excludedFamilies, onGroupsChange, onRequiredFamiliesChange, onExcludedFamiliesChange],
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
    const targetZone = over.id as string
    if (!targetZone.startsWith("zone:")) return
    const data = active.data.current as DragData
    if (data.type === "effect") {
      assignEffect(data.effectId, targetZone)
    } else {
      assignFamily(data.familyName, targetZone)
    }
  }

  // Default click-to-add target: highest-weight group (first in sorted order), or required if no groups
  const defaultClickTarget = sortedGroupIndices.length > 0 ? `zone:group:${sortedGroupIndices[0]}` : "zone:required"

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

          {/* Stacking category exclusions */}
          {stackingCategories.length > 0 && (
            <div>
              <div className="flex items-center gap-2 mb-1">
                <span className="text-sm font-medium">Stacking Category Exclusions</span>
              </div>
              <p className="text-xs text-muted-foreground mb-2">
                All effects in checked categories will be excluded unless individually listed in a priority group below.
              </p>
              <div className="grid sm:grid-cols-2 gap-x-4 gap-y-1 max-h-64 overflow-y-auto">
                {stackingCategories.map((cat) => {
                  const checked = excludedStackingCategories.includes(cat.compatibility_id)
                  return (
                    <Tooltip key={cat.compatibility_id}>
                      <TooltipTrigger asChild>
                        <label className="flex items-center gap-2 text-sm py-0.5 cursor-pointer hover:bg-muted/20 rounded px-1 transition-colors">
                          <Checkbox
                            checked={checked}
                            onCheckedChange={(v: boolean) => {
                              if (v) {
                                onExcludedStackingCategoriesChange([...excludedStackingCategories, cat.compatibility_id])
                              } else {
                                onExcludedStackingCategoriesChange(excludedStackingCategories.filter((id) => id !== cat.compatibility_id))
                              }
                            }}
                          />
                          <span className="truncate">{cat.label}</span>
                          <span className="ml-auto text-xs text-muted-foreground shrink-0">({cat.effect_ids.length})</span>
                        </label>
                      </TooltipTrigger>
                      <TooltipContent side="right" className="max-w-80 max-h-48 overflow-y-auto">
                        <ul className="text-xs space-y-0.5">
                          {cat.member_names.map((name, i) => (
                            <li key={i}>{name}</li>
                          ))}
                        </ul>
                      </TooltipContent>
                    </Tooltip>
                  )
                })}
              </div>
            </div>
          )}
        </div>

        <div className="grid lg:grid-cols-[1fr_320px] gap-6">
          {/* Priority zones */}
          <div className="space-y-5">
            {/* Required */}
            <div>
              <div className="flex items-center gap-3 mb-2">
                <span className="text-xs font-bold uppercase tracking-[0.2em]" style={{ color: REQUIRED_COLOR }}>Required</span>
                <div className="flex-1 h-px opacity-25" style={{ background: REQUIRED_COLOR }} />
                <span className="text-xs text-muted-foreground">hard constraint · always included</span>
              </div>
              <DroppableZone zoneId="zone:required" color={REQUIRED_COLOR}>
                {requiredEffects.length === 0 && requiredFamilies.length === 0 ? (
                  <p className="text-xs text-muted-foreground italic">
                    Drop effects here to require them
                  </p>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {requiredFamilies.map((familyName) => (
                      <DraggableChip
                        key={`family:${familyName}`}
                        dragId={`family:${familyName}`}
                        name={`${familyName} (group)`}
                        color={REQUIRED_COLOR}
                        dragData={{ type: "family", familyName, sourceZone: "zone:required" }}
                        onRemove={() => onRequiredFamiliesChange(requiredFamilies.filter((n) => n !== familyName))}
                      />
                    ))}
                    {requiredEffects.map((id) => {
                      const e = effectMap.get(id)
                      if (!e) return null
                      return (
                        <DraggableChip
                          key={id}
                          dragId={`effect:${id}`}
                          name={e.source === "deep" ? `${e.name} (deep)` : e.name}
                          color={REQUIRED_COLOR}
                          dragData={{ type: "effect", effectId: id, sourceZone: "zone:required" }}
                          onRemove={() => onRequiredEffectsChange(requiredEffects.filter((x) => x !== id))}
                        />
                      )
                    })}
                  </div>
                )}
              </DroppableZone>
            </div>

            {/* Weight groups — same-label groups share one section header */}
            {groupSections.map((section) => (
              <div key={section.label} className="space-y-2">
                <div className="flex items-center gap-3">
                  <span className="text-xs font-bold uppercase tracking-[0.2em]" style={{ color: section.color }}>
                    {section.label}
                  </span>
                  <div className="flex-1 h-px opacity-20" style={{ background: section.color }} />
                </div>
                {section.indices.map((idx) => {
                  const group = groups[idx]
                  const isEmpty = group.effects.length === 0 && group.families.length === 0
                  return (
                    <div key={idx} className="flex items-start gap-2">
                      <WeightInput
                        value={group.weight}
                        onChange={(w) =>
                          onGroupsChange(
                            groups.map((g, i) => i === idx ? { ...g, weight: w } : g),
                          )
                        }
                        className="mt-2 w-14 text-xs text-center bg-transparent border border-border/60 rounded px-1 py-0.5 focus:border-primary focus:outline-none focus:ring-0 shrink-0 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                        title="Group weight (−100 to 100)"
                      />
                      <DroppableZone zoneId={`zone:group:${idx}`} color={section.color} className="flex-1 min-h-[40px]">
                        {isEmpty ? (
                          <p className="text-xs text-muted-foreground italic">
                            Drop effects here, or click in the browser to add
                          </p>
                        ) : (
                          <div className="flex flex-wrap gap-2">
                            {group.families.map((familyName) => (
                              <DraggableChip
                                key={`family:${familyName}`}
                                dragId={`family:${familyName}`}
                                name={`${familyName} (group)`}
                                color={section.color}
                                dragData={{ type: "family", familyName, sourceZone: `zone:group:${idx}` }}
                                onRemove={() =>
                                  onGroupsChange(
                                    groups.map((g, i) =>
                                      i === idx ? { ...g, families: g.families.filter((n) => n !== familyName) } : g,
                                    ),
                                  )
                                }
                              />
                            ))}
                            {group.effects.map((id) => {
                              const e = effectMap.get(id)
                              if (!e) return null
                              return (
                                <DraggableChip
                                  key={id}
                                  dragId={`effect:${id}`}
                                  name={e.source === "deep" ? `${e.name} (deep)` : e.name}
                                  color={section.color}
                                  dragData={{ type: "effect", effectId: id, sourceZone: `zone:group:${idx}` }}
                                  onRemove={() =>
                                    onGroupsChange(
                                      groups.map((g, i) =>
                                        i === idx ? { ...g, effects: g.effects.filter((x) => x !== id) } : g,
                                      ),
                                    )
                                  }
                                />
                              )
                            })}
                          </div>
                        )}
                      </DroppableZone>
                      <button
                        type="button"
                        onClick={() => onGroupsChange(groups.filter((_, i) => i !== idx))}
                        className="mt-2 text-muted-foreground hover:text-destructive transition-colors shrink-0"
                        aria-label="Remove group"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  )
                })}
              </div>
            ))}

            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => onGroupsChange([...groups, { weight: 0, effects: [], families: [] }])}
            >
              <Plus className="h-3.5 w-3.5 mr-1" /> Add Group
            </Button>

            {/* Excluded */}
            <div>
              <div className="flex items-center gap-3 mb-2">
                <span className="text-xs font-bold uppercase tracking-[0.2em]" style={{ color: EXCLUDED_COLOR }}>Excluded</span>
                <div className="flex-1 h-px opacity-25" style={{ background: EXCLUDED_COLOR }} />
                <span className="text-xs text-muted-foreground">blocks relic assignment</span>
              </div>
              <DroppableZone zoneId="zone:excluded" color={EXCLUDED_COLOR}>
                {excludedEffects.length === 0 && excludedFamilies.length === 0 ? (
                  <p className="text-xs text-muted-foreground italic">
                    Drop effects here to block them
                  </p>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {excludedFamilies.map((familyName) => (
                      <DraggableChip
                        key={`family:${familyName}`}
                        dragId={`family:${familyName}`}
                        name={`${familyName} (group)`}
                        color={EXCLUDED_COLOR}
                        dragData={{ type: "family", familyName, sourceZone: "zone:excluded" }}
                        onRemove={() => onExcludedFamiliesChange(excludedFamilies.filter((n) => n !== familyName))}
                      />
                    ))}
                    {excludedEffects.map((id) => {
                      const e = effectMap.get(id)
                      if (!e) return null
                      return (
                        <DraggableChip
                          key={id}
                          dragId={`effect:${id}`}
                          name={e.source === "deep" ? `${e.name} (deep)` : e.name}
                          color={EXCLUDED_COLOR}
                          dragData={{ type: "effect", effectId: id, sourceZone: "zone:excluded" }}
                          onRemove={() => onExcludedEffectsChange(excludedEffects.filter((x) => x !== id))}
                        />
                      )
                    })}
                  </div>
                )}
              </DroppableZone>
            </div>
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
                      data={{ type: "family", familyName: family.name, sourceZone: null }}
                      onClick={() => assignFamily(family.name, defaultClickTarget)}
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
                  data={{ type: "effect", effectId: effect.id, sourceZone: null }}
                  onClick={() => assignEffect(effect.id, defaultClickTarget)}
                >
                  <span className="text-sm truncate flex-1" title={effect.name}>
                    {effect.name}
                    {effect.source === "deep" && (
                      <span className="ml-1.5 text-xs" style={{ color: DEEP_COLOR }}>(deep)</span>
                    )}
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

  const { data: buildRaw } = useSuspenseQuery({
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
  const { data: stackingCategoriesData } = useSuspenseQuery({
    queryKey: ["game", "stacking-categories"],
    queryFn: () => GameService.getStackingCategories() as unknown as Promise<StackingCategory[]>,
    staleTime: Infinity,
  })

  const effects = (effectsData ?? []) as EffectMeta[]
  const families = (familiesData ?? []) as FamilyMeta[]
  const stackingCategories = (stackingCategoriesData ?? []) as StackingCategory[]
  // Cast to new schema type (SDK will be regenerated separately)
  const build = buildRaw as unknown as BuildApiData

  const [groups, setGroups] = useState<WeightGroup[]>(
    () => build.groups ?? DEFAULT_GROUPS.map((g) => ({ ...g })),
  )
  const [requiredEffects, setRequiredEffects] = useState<number[]>(
    () => build.required_effects ?? [],
  )
  const [requiredFamilies, setRequiredFamilies] = useState<string[]>(
    () => build.required_families ?? [],
  )
  const [excludedEffects, setExcludedEffects] = useState<number[]>(
    () => build.excluded_effects ?? [],
  )
  const [excludedFamilies, setExcludedFamilies] = useState<string[]>(
    () => build.excluded_families ?? [],
  )
  const [includeDeep, setIncludeDeep] = useState(build.include_deep)
  const [curseMax, setCurseMax] = useState(build.curse_max)
  const [pinnedRelics, setPinnedRelics] = useState<number[]>(build.pinned_relics ?? [])
  const [pinnedRelicMeta, setPinnedRelicMeta] = useState<Map<number, RelicForPicker>>(new Map())
  const [excludedStackingCategories, setExcludedStackingCategories] = useState<number[]>(
    () => build.excluded_stacking_categories ?? [],
  )

  const groupsRef = useRef(groups)
  const requiredEffectsRef = useRef(requiredEffects)
  const requiredFamiliesRef = useRef(requiredFamilies)
  const excludedEffectsRef = useRef(excludedEffects)
  const excludedFamiliesRef = useRef(excludedFamilies)
  const includeDeepRef = useRef(includeDeep)
  const curseMaxRef = useRef(curseMax)
  const pinnedRelicsRef = useRef(pinnedRelics)
  const excludedStackingCategoriesRef = useRef(excludedStackingCategories)
  groupsRef.current = groups
  requiredEffectsRef.current = requiredEffects
  requiredFamiliesRef.current = requiredFamilies
  excludedEffectsRef.current = excludedEffects
  excludedFamiliesRef.current = excludedFamilies
  includeDeepRef.current = includeDeep
  curseMaxRef.current = curseMax
  pinnedRelicsRef.current = pinnedRelics
  excludedStackingCategoriesRef.current = excludedStackingCategories

  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // On mount: populate pinnedRelicMeta for handles already stored in the build
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
    setGroups(build.groups ?? DEFAULT_GROUPS.map((g) => ({ ...g })))
    setRequiredEffects(build.required_effects ?? [])
    setRequiredFamilies(build.required_families ?? [])
    setExcludedEffects(build.excluded_effects ?? [])
    setExcludedFamilies(build.excluded_families ?? [])
    setIncludeDeep(build.include_deep)
    setCurseMax(build.curse_max)
    setPinnedRelics(build.pinned_relics ?? [])
    setExcludedStackingCategories(build.excluded_stacking_categories ?? [])
  }, [build])

  const saveMutation = useMutation({
    mutationFn: () =>
      BuildsService.updateBuild({
        buildId,
        requestBody: {
          groups: groupsRef.current,
          required_effects: requiredEffectsRef.current,
          required_families: requiredFamiliesRef.current,
          excluded_effects: excludedEffectsRef.current,
          excluded_families: excludedFamiliesRef.current,
          excluded_stacking_categories: excludedStackingCategoriesRef.current,
          include_deep: includeDeepRef.current,
          curse_max: curseMaxRef.current,
          pinned_relics: pinnedRelicsRef.current,
        } as any, // SDK will be regenerated with new schema
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["builds"] }),
    onError: handleError.bind(showErrorToast),
  })

  // Store flush function in ref so cleanup always calls latest version
  const flushSaveRef = useRef(() => saveMutation.mutate())
  flushSaveRef.current = () => saveMutation.mutate()

  useEffect(() => {
    return () => {
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current)
        flushSaveRef.current()
      }
    }
  }, [])

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
      name={(buildRaw as any).name}
      character={(buildRaw as any).character}
      groups={groups}
      requiredEffects={requiredEffects}
      requiredFamilies={requiredFamilies}
      excludedEffects={excludedEffects}
      excludedFamilies={excludedFamilies}
      includeDeep={includeDeep}
      curseMax={curseMax}
      pinnedRelics={pinnedRelics}
      pinnedRelicMeta={pinnedRelicMeta}
      excludedStackingCategories={excludedStackingCategories}
      stackingCategories={stackingCategories}
      saving={saveMutation.isPending}
      effects={effects}
      families={families}
      isAuth={true}
      onGroupsChange={(g) => { setGroups(g); scheduleAutoSave() }}
      onRequiredEffectsChange={(ids) => { setRequiredEffects(ids); scheduleAutoSave() }}
      onRequiredFamiliesChange={(names) => { setRequiredFamilies(names); scheduleAutoSave() }}
      onExcludedEffectsChange={(ids) => { setExcludedEffects(ids); scheduleAutoSave() }}
      onExcludedFamiliesChange={(names) => { setExcludedFamilies(names); scheduleAutoSave() }}
      onIncludeDeepChange={(v) => { setIncludeDeep(v); scheduleAutoSave() }}
      onCurseMaxChange={(v) => { setCurseMax(v); scheduleAutoSave() }}
      onPinnedRelicsChange={(handles, meta) => {
        setPinnedRelics(handles)
        setPinnedRelicMeta(meta)
        scheduleAutoSave()
      }}
      onExcludedStackingCategoriesChange={(ids) => { setExcludedStackingCategories(ids); scheduleAutoSave() }}
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
  const { data: stackingCategoriesData } = useSuspenseQuery({
    queryKey: ["game", "stacking-categories"],
    queryFn: () => GameService.getStackingCategories() as unknown as Promise<StackingCategory[]>,
    staleTime: Infinity,
  })

  const effects = (effectsData ?? []) as EffectMeta[]
  const families = (familiesData ?? []) as FamilyMeta[]
  const stackingCategories = (stackingCategoriesData ?? []) as StackingCategory[]
  const build = getById(buildId)

  const [groups, setGroups] = useState<WeightGroup[]>(
    () => build?.groups ?? DEFAULT_GROUPS.map((g) => ({ ...g })),
  )
  const [requiredEffects, setRequiredEffects] = useState<number[]>(
    () => build?.required_effects ?? [],
  )
  const [requiredFamilies, setRequiredFamilies] = useState<string[]>(
    () => build?.required_families ?? [],
  )
  const [excludedEffects, setExcludedEffects] = useState<number[]>(
    () => build?.excluded_effects ?? [],
  )
  const [excludedFamilies, setExcludedFamilies] = useState<string[]>(
    () => build?.excluded_families ?? [],
  )
  const [includeDeep, setIncludeDeep] = useState(build?.include_deep ?? false)
  const [curseMax, setCurseMax] = useState(build?.curse_max ?? 1)
  const [pinnedRelics, setPinnedRelics] = useState<number[]>(build?.pinned_relics ?? [])
  const [pinnedRelicMeta, setPinnedRelicMeta] = useState<Map<number, RelicForPicker>>(() => {
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
  const [excludedStackingCategories, setExcludedStackingCategories] = useState<number[]>(
    () => build?.excluded_stacking_categories ?? [],
  )

  const groupsRef = useRef(groups)
  const requiredEffectsRef = useRef(requiredEffects)
  const requiredFamiliesRef = useRef(requiredFamilies)
  const excludedEffectsRef = useRef(excludedEffects)
  const excludedFamiliesRef = useRef(excludedFamilies)
  const includeDeepRef = useRef(includeDeep)
  const curseMaxRef = useRef(curseMax)
  const pinnedRelicsRef = useRef(pinnedRelics)
  const excludedStackingCategoriesRef = useRef(excludedStackingCategories)
  groupsRef.current = groups
  requiredEffectsRef.current = requiredEffects
  requiredFamiliesRef.current = requiredFamilies
  excludedEffectsRef.current = excludedEffects
  excludedFamiliesRef.current = excludedFamilies
  includeDeepRef.current = includeDeep
  curseMaxRef.current = curseMax
  pinnedRelicsRef.current = pinnedRelics
  excludedStackingCategoriesRef.current = excludedStackingCategories

  const updateRef = useRef(update)
  updateRef.current = update

  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Store flush function in ref so cleanup always calls latest version
  const flushSaveRef = useRef(() => {
    updateRef.current(buildId, {
      groups: groupsRef.current,
      required_effects: requiredEffectsRef.current,
      required_families: requiredFamiliesRef.current,
      excluded_effects: excludedEffectsRef.current,
      excluded_families: excludedFamiliesRef.current,
      excluded_stacking_categories: excludedStackingCategoriesRef.current,
      include_deep: includeDeepRef.current,
      curse_max: curseMaxRef.current,
      pinned_relics: pinnedRelicsRef.current,
    })
  })
  flushSaveRef.current = () => {
    updateRef.current(buildId, {
      groups: groupsRef.current,
      required_effects: requiredEffectsRef.current,
      required_families: requiredFamiliesRef.current,
      excluded_effects: excludedEffectsRef.current,
      excluded_families: excludedFamiliesRef.current,
      excluded_stacking_categories: excludedStackingCategoriesRef.current,
      include_deep: includeDeepRef.current,
      curse_max: curseMaxRef.current,
      pinned_relics: pinnedRelicsRef.current,
    })
  }

  useEffect(() => {
    return () => {
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current)
        flushSaveRef.current()
      }
    }
  }, [])

  const scheduleAutoSave = useCallback(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    saveTimerRef.current = setTimeout(() => {
      updateRef.current(buildId, {
        groups: groupsRef.current,
        required_effects: requiredEffectsRef.current,
        required_families: requiredFamiliesRef.current,
        excluded_effects: excludedEffectsRef.current,
        excluded_families: excludedFamiliesRef.current,
        excluded_stacking_categories: excludedStackingCategoriesRef.current,
        include_deep: includeDeepRef.current,
        curse_max: curseMaxRef.current,
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
      groups={groups}
      requiredEffects={requiredEffects}
      requiredFamilies={requiredFamilies}
      excludedEffects={excludedEffects}
      excludedFamilies={excludedFamilies}
      includeDeep={includeDeep}
      curseMax={curseMax}
      pinnedRelics={pinnedRelics}
      pinnedRelicMeta={pinnedRelicMeta}
      excludedStackingCategories={excludedStackingCategories}
      stackingCategories={stackingCategories}
      saving={false}
      effects={effects}
      families={families}
      isAuth={false}
      onGroupsChange={(g) => { setGroups(g); scheduleAutoSave() }}
      onRequiredEffectsChange={(ids) => { setRequiredEffects(ids); scheduleAutoSave() }}
      onRequiredFamiliesChange={(names) => { setRequiredFamilies(names); scheduleAutoSave() }}
      onExcludedEffectsChange={(ids) => { setExcludedEffects(ids); scheduleAutoSave() }}
      onExcludedFamiliesChange={(names) => { setExcludedFamilies(names); scheduleAutoSave() }}
      onIncludeDeepChange={(v) => { setIncludeDeep(v); scheduleAutoSave() }}
      onCurseMaxChange={(v) => { setCurseMax(v); scheduleAutoSave() }}
      onPinnedRelicsChange={(handles, meta) => {
        setPinnedRelics(handles)
        setPinnedRelicMeta(meta)
        scheduleAutoSave()
      }}
      onExcludedStackingCategoriesChange={(ids) => { setExcludedStackingCategories(ids); scheduleAutoSave() }}
      onRename={(name) => updateRef.current(buildId, { name })}
    />
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function BuildEditorPage() {
  const { buildId } = useParams({ from: "/_layout/builds/$buildId/edit" })

  return (
    <Suspense fallback={<Skeleton className="h-64 w-full" />}>
      {isLoggedIn()
        ? <AuthBuildEditorContent buildId={buildId} />
        : <LocalBuildEditorContent buildId={buildId} />
      }
    </Suspense>
  )
}
