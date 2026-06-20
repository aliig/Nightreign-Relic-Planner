import { useQuery, useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { ChevronDown, ChevronUp, Pencil, RotateCcw, Trash2 } from "lucide-react"
import { type ReactNode, Suspense, useMemo, useState } from "react"

import { GameService, type ParsedLoadoutData, SavesService } from "@/client"
import { CumulativeSummary } from "@/components/OptimizeResults"
import {
  buildEffectMap,
  EMPTY_EFFECT,
  RelicNameCell,
} from "@/components/RelicDisplay"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import useAuth from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"
import {
  addLoadoutOp,
  removeLoadoutOp,
  usePendingSlot,
  useReconcileSlotBases,
} from "@/lib/pendingChanges"

export const Route = createFileRoute("/_layout/loadouts")({
  component: LoadoutsPage,
  head: () => ({
    meta: [{ title: "Game Loadouts - Nightreign Relic Planner" }],
  }),
})

const CAPACITY = 100

type SlotRelic = {
  name: string
  color: string
  tier: string
  isDeep: boolean
  effects: number[]
  curses: number[]
}

// --- rich loadout card (mirrors the optimizer's vessel card, minus scoring) --

function effectNames(ids: number[], effectMap: Map<number, string>): string[] {
  const out: string[] = []
  for (const id of ids) {
    if (id === 0 || id === EMPTY_EFFECT) continue
    const name = effectMap.get(id)
    if (name) out.push(name)
  }
  return out
}

function RelicCell({
  handle,
  relic,
  effectMap,
}: {
  handle: number
  relic?: SlotRelic
  effectMap: Map<number, string>
}) {
  if (handle === 0 || !relic) {
    return (
      <div className="rounded-md border p-3 text-xs italic text-muted-foreground">
        {handle === 0 ? "No relic assigned" : "Relic not in current inventory"}
      </div>
    )
  }
  const effects = effectNames(relic.effects, effectMap)
  const curses = effectNames(relic.curses, effectMap)
  return (
    <div className="space-y-1 rounded-md border p-3">
      <RelicNameCell
        name={relic.name}
        color={relic.color}
        tier={relic.tier}
        isDeep={relic.isDeep}
      />
      {effects.length > 0 && (
        <div className="mt-1 space-y-0.5">
          {effects.map((n) => (
            <div key={n} className="truncate text-xs">
              {n}
            </div>
          ))}
        </div>
      )}
      {curses.length > 0 && (
        <div className="mt-1.5 space-y-0.5 border-t border-destructive/20 pt-1.5">
          {curses.map((n) => (
            <div key={n} className="truncate text-xs text-destructive/80">
              {n}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function LoadoutCard({
  name,
  vesselName,
  cumulative,
  gaHandles,
  relicByHandle,
  effectMap,
  actions,
}: {
  name: ReactNode
  vesselName: string
  cumulative?: ParsedLoadoutData["cumulative_effects"]
  gaHandles: number[]
  relicByHandle: Map<number, SlotRelic>
  effectMap: Map<number, string>
  actions?: ReactNode
}) {
  const [expanded, setExpanded] = useState(true)
  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <button
            type="button"
            className="min-w-0 flex-1 text-left"
            onClick={() => setExpanded((v) => !v)}
          >
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <CardTitle className="text-base">{name}</CardTitle>
            </div>
            <p className="mt-0.5 text-xs text-muted-foreground">{vesselName}</p>
          </button>
          <div className="flex shrink-0 items-center gap-1">
            {actions}
            <Button
              variant="ghost"
              size="sm"
              className="px-1.5"
              aria-label={expanded ? "Collapse" : "Expand"}
              onClick={() => setExpanded((v) => !v)}
            >
              {expanded ? (
                <ChevronUp className="h-4 w-4 text-muted-foreground" />
              ) : (
                <ChevronDown className="h-4 w-4 text-muted-foreground" />
              )}
            </Button>
          </div>
        </div>
      </CardHeader>
      {cumulative && cumulative.length > 0 && (
        <CumulativeSummary groups={cumulative} />
      )}
      {expanded && (
        <CardContent className="pt-0">
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {gaHandles.map((h, i) => (
              <RelicCell
                key={`${i}-${h}`}
                handle={h}
                relic={relicByHandle.get(h)}
                effectMap={effectMap}
              />
            ))}
          </div>
        </CardContent>
      )}
    </Card>
  )
}

// --- shared manager UI ------------------------------------------------------

// A loadout as shown in the live list: an existing in-game preset (with any
// rename/overwrite already applied) or a not-yet-saved new one.
type DisplayLoadout = {
  key: string
  character: string
  name: string
  vesselName: string
  gaHandles: number[]
  cumulative?: ParsedLoadoutData["cumulative_effects"]
  source: { kind: "existing"; index: number; rawName: string } | { kind: "add" }
  opId?: string // for adds — remove op on delete
}

function LoadoutManager({
  loadouts,
  relicByHandle,
  effectMap,
  slotIndex,
}: {
  loadouts: ParsedLoadoutData[]
  relicByHandle: Map<number, SlotRelic>
  effectMap: Map<number, string>
  slotIndex: number
}) {
  const pending = usePendingSlot(slotIndex)
  const [renaming, setRenaming] = useState<{
    index: number
    name: string
  } | null>(null)
  const [renameDraft, setRenameDraft] = useState("")

  // Bucket this slot's pending loadout ops by kind/index.
  const renameByIndex = new Map<number, { id: string; name: string }>()
  const deleteByIndex = new Map<number, string>()
  const overwriteByIndex = new Map<
    number,
    { id: string; ga_handles: number[]; name?: string }
  >()
  const adds: Array<{
    id: string
    name: string
    character: string
    vesselName?: string
    ga_handles: number[]
  }> = []
  let resetVesselsId: string | undefined
  let resetPresetsId: string | undefined
  for (const op of pending.loadoutOps) {
    if (op.kind === "rename")
      renameByIndex.set(op.index, { id: op.id, name: op.name })
    else if (op.kind === "delete") deleteByIndex.set(op.index, op.id)
    else if (op.kind === "overwrite")
      overwriteByIndex.set(op.index, {
        id: op.id,
        ga_handles: op.ga_handles,
        name: op.name,
      })
    else if (op.kind === "add")
      adds.push({
        id: op.id,
        name: op.name,
        character: op.character,
        vesselName: op.vesselName,
        ga_handles: op.ga_handles,
      })
    else if (op.kind === "reset_vessels") resetVesselsId = op.id
    else if (op.kind === "reset_presets") resetPresetsId = op.id
  }
  const resetPresets = resetPresetsId !== undefined
  const resetVessels = resetVesselsId !== undefined
  const hasOtherLoadoutEdits =
    renameByIndex.size +
      deleteByIndex.size +
      overwriteByIndex.size +
      adds.length >
    0
  const used = resetPresets
    ? adds.length
    : loadouts.length + adds.length - deleteByIndex.size

  // New/overwritten setups have relic sets the backend never scored — compute
  // their cumulative bonuses live so they render identically to saved loadouts.
  // (Rebuilt each render from the ops above; the useQuery below memoizes the
  // actual network call by ga_handle signature.)
  const dynamic: Array<{ id: string; ga_handles: number[] }> = []
  for (const a of adds)
    dynamic.push({ id: `add-${a.id}`, ga_handles: a.ga_handles })
  for (const [index, ov] of overwriteByIndex)
    dynamic.push({ id: `ov-${index}`, ga_handles: ov.ga_handles })

  const dynamicGroups = dynamic.map(({ ga_handles }) => {
    const ids: number[] = []
    for (const h of ga_handles) {
      const r = relicByHandle.get(h)
      if (r) ids.push(...r.effects, ...r.curses)
    }
    return ids
  })
  const dynamicSig = JSON.stringify(dynamic.map((d) => d.ga_handles))
  const { data: dynamicCumulative } = useQuery({
    queryKey: ["cumulative-dynamic", slotIndex, dynamicSig],
    queryFn: () =>
      GameService.cumulativeEffects({
        requestBody: { effect_id_groups: dynamicGroups },
      }),
    enabled: dynamic.length > 0,
    staleTime: Number.POSITIVE_INFINITY,
  })
  const cumulativeById = new Map<
    string,
    ParsedLoadoutData["cumulative_effects"]
  >()
  dynamic.forEach((d, i) => {
    if (dynamicCumulative?.[i]) cumulativeById.set(d.id, dynamicCumulative[i])
  })

  // Build the live list: existing presets (deleted ones dropped, renames and
  // overwrites applied) plus the new ones, all looking like real loadouts.
  const display: DisplayLoadout[] = []
  if (!resetPresets) {
    for (const l of loadouts) {
      if (deleteByIndex.has(l.index)) continue
      const ov = overwriteByIndex.get(l.index)
      display.push({
        key: `idx-${l.index}`,
        character: l.character,
        name: renameByIndex.get(l.index)?.name ?? l.name,
        vesselName: l.vessel_name,
        gaHandles: ov ? ov.ga_handles : (l.ga_handles ?? []),
        cumulative: ov
          ? cumulativeById.get(`ov-${l.index}`)
          : l.cumulative_effects,
        source: { kind: "existing", index: l.index, rawName: l.name },
      })
    }
  }
  for (const a of adds) {
    display.push({
      key: `add-${a.id}`,
      character: a.character,
      name: a.name,
      vesselName: a.vesselName ?? "",
      gaHandles: a.ga_handles,
      cumulative: cumulativeById.get(`add-${a.id}`),
      source: { kind: "add" },
      opId: a.id,
    })
  }

  const groupedMap = new Map<string, DisplayLoadout[]>()
  for (const d of display) {
    const list = groupedMap.get(d.character) ?? []
    list.push(d)
    groupedMap.set(d.character, list)
  }
  const grouped = [...groupedMap.entries()].sort((a, b) =>
    a[0].localeCompare(b[0]),
  )

  function deleteExisting(index: number, rawName: string) {
    const ren = renameByIndex.get(index)
    if (ren) removeLoadoutOp(slotIndex, ren.id)
    const ov = overwriteByIndex.get(index)
    if (ov) removeLoadoutOp(slotIndex, ov.id)
    addLoadoutOp(slotIndex, { kind: "delete", index, name: rawName })
  }

  function openRename(d: DisplayLoadout) {
    if (d.source.kind !== "existing") return
    setRenaming({ index: d.source.index, name: d.source.rawName })
    setRenameDraft(renameByIndex.get(d.source.index)?.name ?? d.source.rawName)
  }

  function saveRename() {
    if (!renaming) return
    const name = renameDraft.trim()
    const existing = renameByIndex.get(renaming.index)
    if (existing) removeLoadoutOp(slotIndex, existing.id)
    if (name && name !== renaming.name) {
      addLoadoutOp(slotIndex, {
        kind: "rename",
        index: renaming.index,
        name,
        oldName: renaming.name,
      })
    }
    setRenaming(null)
  }

  function toggleResetVessels() {
    if (resetVesselsId) removeLoadoutOp(slotIndex, resetVesselsId)
    else addLoadoutOp(slotIndex, { kind: "reset_vessels" })
  }

  function toggleResetPresets() {
    if (resetPresetsId) {
      removeLoadoutOp(slotIndex, resetPresetsId)
      return
    }
    // reset_presets cannot combine with other loadout edits — clear them first.
    for (const op of pending.loadoutOps) {
      if (op.kind !== "reset_vessels") removeLoadoutOp(slotIndex, op.id)
    }
    addLoadoutOp(slotIndex, { kind: "reset_presets" })
  }

  const empty = display.length === 0

  return (
    <div className="space-y-5">
      <GlobalControls
        used={used}
        resetVessels={resetVessels}
        resetPresets={resetPresets}
        onToggleVessels={toggleResetVessels}
        onTogglePresets={toggleResetPresets}
        canResetPresets={loadouts.length > 0 && !hasOtherLoadoutEdits}
        hasLoadouts={loadouts.length > 0}
      />

      {empty && (
        <p className="py-8 text-center text-muted-foreground">
          {resetPresets
            ? "All loadouts will be cleared on export — undo from the changes panel up top."
            : "This character has no saved relic loadouts. Optimize a build and use Save as loadout to add one."}
        </p>
      )}

      {grouped.map(([character, list]) => (
        <section key={character} className="space-y-2">
          <SectionHeader title={character} count={list.length} />
          <div className="grid gap-2">
            {list.map((d) => (
              <LoadoutCard
                key={d.key}
                name={
                  d.name || (
                    <span className="italic text-muted-foreground">
                      (unnamed)
                    </span>
                  )
                }
                vesselName={d.vesselName}
                cumulative={d.cumulative}
                gaHandles={d.gaHandles}
                relicByHandle={relicByHandle}
                effectMap={effectMap}
                actions={
                  d.source.kind === "existing" ? (
                    <>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="px-1.5"
                        onClick={() => openRename(d)}
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="px-1.5"
                        onClick={() =>
                          d.source.kind === "existing" &&
                          deleteExisting(d.source.index, d.source.rawName)
                        }
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </>
                  ) : (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="px-1.5"
                      onClick={() =>
                        d.opId && removeLoadoutOp(slotIndex, d.opId)
                      }
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  )
                }
              />
            ))}
          </div>
        </section>
      ))}

      {/* Rename dialog */}
      <Dialog
        open={renaming !== null}
        onOpenChange={(o) => !o && setRenaming(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Rename loadout</DialogTitle>
            <DialogDescription>Up to 18 characters.</DialogDescription>
          </DialogHeader>
          <Input
            value={renameDraft}
            maxLength={18}
            onChange={(e) => setRenameDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && saveRename()}
          />
          <div className="text-xs text-muted-foreground">
            {renameDraft.length}/18
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRenaming(null)}>
              Cancel
            </Button>
            <Button onClick={saveRename}>Rename</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function SectionHeader({ title, count }: { title: string; count: number }) {
  return (
    <div className="flex items-center gap-3 pt-1">
      <h3 className="text-base font-bold tracking-tight">{title}</h3>
      <span className="text-xs text-muted-foreground">
        {count} loadout{count !== 1 ? "s" : ""}
      </span>
      <div className="h-px flex-1 bg-border" />
    </div>
  )
}

function GlobalControls({
  used,
  resetVessels,
  resetPresets,
  onToggleVessels,
  onTogglePresets,
  canResetPresets,
  hasLoadouts,
}: {
  used: number
  resetVessels: boolean
  resetPresets: boolean
  onToggleVessels: () => void
  onTogglePresets: () => void
  canResetPresets: boolean
  hasLoadouts: boolean
}) {
  const [confirmOpen, setConfirmOpen] = useState(false)
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <Badge variant="secondary">
        {used} / {CAPACITY} loadouts used
      </Badge>
      <div className="flex flex-wrap gap-2">
        <Button
          variant={resetVessels ? "destructive" : "outline"}
          size="sm"
          onClick={onToggleVessels}
        >
          <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
          {resetVessels ? "Vessels reset — undo" : "Reset all vessels"}
        </Button>
        {hasLoadouts && (
          <>
            <Button
              variant={resetPresets ? "destructive" : "outline"}
              size="sm"
              disabled={!resetPresets && !canResetPresets}
              onClick={() => {
                if (resetPresets) onTogglePresets()
                else setConfirmOpen(true)
              }}
              title={
                !resetPresets && !canResetPresets
                  ? "Undo your other loadout edits first"
                  : undefined
              }
            >
              <Trash2 className="mr-1.5 h-3.5 w-3.5" />
              {resetPresets
                ? "All loadouts cleared — undo"
                : "Reset all loadouts"}
            </Button>
            <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Delete all loadout presets?</DialogTitle>
                  <DialogDescription>
                    This queues deletion of every loadout preset in this save.
                    Nothing is written to your save until you export — and you
                    can undo it from the Changes panel first.
                  </DialogDescription>
                </DialogHeader>
                <DialogFooter>
                  <Button variant="ghost" onClick={() => setConfirmOpen(false)}>
                    Cancel
                  </Button>
                  <Button
                    variant="destructive"
                    onClick={() => {
                      onTogglePresets()
                      setConfirmOpen(false)
                    }}
                  >
                    Queue delete-all
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </>
        )}
      </div>
    </div>
  )
}

// --- relic-join helpers -----------------------------------------------------

function relicMapFromList(
  list: Array<Record<string, any>>,
): Map<number, SlotRelic> {
  const m = new Map<number, SlotRelic>()
  for (const r of list) {
    m.set(Number(r.ga_handle), {
      name: r.name,
      color: r.color,
      tier: r.tier,
      isDeep: !!r.is_deep,
      effects: [r.effect_1, r.effect_2, r.effect_3],
      curses: [r.curse_1, r.curse_2, r.curse_3],
    })
  }
  return m
}

// --- authenticated ----------------------------------------------------------

function AuthLoadoutsBody({
  profileId,
  slotIndex,
  effectMap,
}: {
  profileId: string
  slotIndex: number
  effectMap: Map<number, string>
}) {
  const { data: loadouts } = useSuspenseQuery({
    queryKey: ["loadouts", profileId],
    queryFn: () => SavesService.getProfileLoadouts({ profileId }),
    staleTime: 5 * 60 * 1000,
  })
  const { data: relics } = useSuspenseQuery({
    queryKey: ["relics", profileId],
    queryFn: () => SavesService.getProfileRelics({ profileId }),
    staleTime: 5 * 60 * 1000,
  })

  const relicByHandle = useMemo(
    () => relicMapFromList(relics.data ?? []),
    [relics.data],
  )

  return (
    <LoadoutManager
      loadouts={loadouts.data ?? []}
      relicByHandle={relicByHandle}
      effectMap={effectMap}
      slotIndex={slotIndex}
    />
  )
}

function AuthLoadouts() {
  const { data: profiles } = useSuspenseQuery({
    queryKey: ["profiles"],
    queryFn: () => SavesService.listProfiles(),
    staleTime: 5 * 60 * 1000,
  })
  const { data: effectsData } = useSuspenseQuery({
    queryKey: ["game", "effects"],
    queryFn: () => GameService.getEffects(),
    staleTime: Number.POSITIVE_INFINITY,
  })
  const effectMap = useMemo(
    () => buildEffectMap((effectsData ?? []) as unknown[]),
    [effectsData],
  )
  const [selectedId, setSelectedId] = useState<string | null>(
    profiles.data?.[0]?.id ?? null,
  )

  const { showErrorToast } = useCustomToast()
  // Drop any pending loadout edits whose underlying save was re-uploaded (here
  // or on another device) since they were made — index-based ops would mis-fire.
  useReconcileSlotBases(
    (profiles.data ?? []).map((p) => ({ slot: p.slot_index, id: p.id })),
    (slots) =>
      showErrorToast(
        `Cleared unsynced edits for slot ${slots.join(", ")} — this save was re-uploaded since you made them.`,
      ),
  )

  if (!profiles.data?.length) {
    return <NoProfiles />
  }
  const selected =
    profiles.data.find((p) => p.id === selectedId) ?? profiles.data[0]

  return (
    <div className="space-y-4">
      <ProfileSelector
        options={profiles.data.map((p) => ({
          value: p.id,
          label: `${p.name} (Slot ${p.slot_index})`,
          name: p.name,
        }))}
        value={selected.id}
        onChange={setSelectedId}
      />
      <Suspense fallback={<Skeleton className="h-48 w-full" />}>
        <AuthLoadoutsBody
          key={selected.id}
          profileId={selected.id}
          slotIndex={selected.slot_index}
          effectMap={effectMap}
        />
      </Suspense>
    </div>
  )
}

// --- anonymous --------------------------------------------------------------

function AnonLoadouts() {
  const { data: effectsData } = useSuspenseQuery({
    queryKey: ["game", "effects"],
    queryFn: () => GameService.getEffects(),
    staleTime: Number.POSITIVE_INFINITY,
  })
  const effectMap = useMemo(
    () => buildEffectMap((effectsData ?? []) as unknown[]),
    [effectsData],
  )

  const allProfiles: Array<Record<string, any>> = JSON.parse(
    sessionStorage.getItem("parsedProfiles") ?? "[]",
  )
  const defaultProfile = (() => {
    try {
      return JSON.parse(sessionStorage.getItem("selectedProfile") ?? "null")
    } catch {
      return null
    }
  })()
  const defaultSlot =
    defaultProfile?.slot_index ?? allProfiles[0]?.slot_index ?? null
  const [selectedSlot, setSelectedSlot] = useState<number | null>(defaultSlot)

  const profile =
    allProfiles.find((c) => c.slot_index === selectedSlot) ??
    allProfiles[0] ??
    null
  const relicByHandle = relicMapFromList(profile?.relics ?? [])
  const loadouts = (profile?.presets ?? []) as ParsedLoadoutData[]

  // Cumulative effects aren't stored in the anonymous (session-only) payload, so
  // compute them via the stateless game endpoint — same % bonuses as the optimizer.
  const effectGroups = loadouts.map((l) => {
    const ids: number[] = []
    for (const h of l.ga_handles ?? []) {
      const r = relicByHandle.get(h)
      if (r) ids.push(...r.effects, ...r.curses)
    }
    return ids
  })
  const { data: computed } = useQuery({
    queryKey: ["cumulative", profile?.slot_index, loadouts.length],
    queryFn: () =>
      GameService.cumulativeEffects({
        requestBody: { effect_id_groups: effectGroups },
      }),
    enabled: loadouts.length > 0,
    staleTime: Number.POSITIVE_INFINITY,
  })
  const enriched = loadouts.map((l, i) =>
    computed?.[i] ? { ...l, cumulative_effects: computed[i] } : l,
  )

  const handleChange = (slotStr: string) => {
    const slot = Number(slotStr)
    setSelectedSlot(slot)
    const picked = allProfiles.find((c) => c.slot_index === slot)
    if (picked)
      sessionStorage.setItem("selectedProfile", JSON.stringify(picked))
  }

  if (allProfiles.length === 0) return <NoProfiles />

  return (
    <div className="space-y-4">
      <ProfileSelector
        options={allProfiles.map((p) => ({
          value: String(p.slot_index),
          label: `${p.name} (Slot ${p.slot_index})`,
          name: p.name,
        }))}
        value={String(profile?.slot_index ?? "")}
        onChange={handleChange}
        anon
      />
      <LoadoutManager
        key={profile?.slot_index}
        loadouts={enriched}
        relicByHandle={relicByHandle}
        effectMap={effectMap}
        slotIndex={Number(profile?.slot_index ?? 0)}
      />
    </div>
  )
}

// --- shared bits ------------------------------------------------------------

function ProfileSelector({
  options,
  value,
  onChange,
  anon,
}: {
  options: { value: string; label: string; name: string }[]
  value: string
  onChange: (v: string) => void
  anon?: boolean
}) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      {options.length > 1 ? (
        <Select value={value} onValueChange={onChange}>
          <SelectTrigger className="w-56">
            <SelectValue placeholder="Select character" />
          </SelectTrigger>
          <SelectContent>
            {options.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {o.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : (
        <p className="text-sm text-muted-foreground">
          <strong>{options[0]?.name}</strong>
        </p>
      )}
      {anon && (
        <p className="text-sm text-muted-foreground">
          Session only —{" "}
          <a href="/login" className="underline">
            sign in
          </a>{" "}
          to save.
        </p>
      )}
    </div>
  )
}

function NoProfiles() {
  return (
    <p className="py-8 text-center text-muted-foreground">
      No save loaded.{" "}
      <a href="/upload" className="underline">
        Upload a save file
      </a>{" "}
      first.
    </p>
  )
}

function LoadoutsPage() {
  const { user } = useAuth()
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Game Loadouts</h1>
        <p className="mt-1 text-muted-foreground">
          View, rename, and delete the in-game relic loadout presets saved in
          your character's save file. Expand a loadout to see every relic's
          effects and its cumulative bonuses. Create new ones from the optimizer
          with <strong>Save as loadout</strong>.
        </p>
      </div>
      <Suspense fallback={<Skeleton className="h-48 w-full" />}>
        {user ? <AuthLoadouts /> : <AnonLoadouts />}
      </Suspense>
    </div>
  )
}
