import { useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import {
  AlertTriangle,
  Download,
  Pencil,
  RotateCcw,
  Trash2,
} from "lucide-react"
import { Suspense, useMemo, useState } from "react"

import { type ParsedLoadoutData, SavesService } from "@/client"
import { COLOR_HEX } from "@/components/RelicDisplay"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
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
  exportModifiedLoadouts,
  LoadoutExportError,
  type LoadoutOp,
} from "@/lib/exportLoadouts"
import { getSaveFile, getSaveFileMeta, rememberSaveFile } from "@/lib/saveFile"

export const Route = createFileRoute("/_layout/loadouts")({
  component: LoadoutsPage,
  head: () => ({
    meta: [{ title: "Relic Loadouts - Nightreign Relic Planner" }],
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

// --- shared manager UI ------------------------------------------------------

function LoadoutManager({
  loadouts,
  relicByHandle,
  slotIndex,
}: {
  loadouts: ParsedLoadoutData[]
  relicByHandle: Map<number, SlotRelic>
  slotIndex: number
}) {
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [renames, setRenames] = useState<Record<number, string>>({})
  const [deletes, setDeletes] = useState<Set<number>>(new Set())
  const [resetVessels, setResetVessels] = useState(false)
  const [resetPresets, setResetPresets] = useState(false)
  const [renaming, setRenaming] = useState<ParsedLoadoutData | null>(null)
  const [renameDraft, setRenameDraft] = useState("")
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [, setFileTick] = useState(0)

  const saveFile = getSaveFile()
  const saveMeta = getSaveFileMeta()

  const renameCount = Object.keys(renames).length
  const deleteCount = deletes.size
  const hasChanges =
    renameCount > 0 || deleteCount > 0 || resetVessels || resetPresets
  const used = loadouts.length - (resetPresets ? loadouts.length : deletes.size)

  // Group loadouts by character (hero class), preserving chain order.
  const grouped = useMemo(() => {
    const m = new Map<string, ParsedLoadoutData[]>()
    for (const l of loadouts) {
      const list = m.get(l.character) ?? []
      list.push(l)
      m.set(l.character, list)
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [loadouts])

  function toggleDelete(index: number) {
    setDeletes((prev) => {
      const next = new Set(prev)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })
  }

  function openRename(l: ParsedLoadoutData) {
    setRenaming(l)
    setRenameDraft(renames[l.index] ?? l.name)
  }

  function saveRename() {
    if (!renaming) return
    const name = renameDraft.trim()
    setRenames((prev) => {
      const next = { ...prev }
      if (name === renaming.name || name === "") delete next[renaming.index]
      else next[renaming.index] = name
      return next
    })
    setRenaming(null)
  }

  function clearAll() {
    setRenames({})
    setDeletes(new Set())
    setResetVessels(false)
    setResetPresets(false)
  }

  function buildOps(): LoadoutOp[] {
    const ops: LoadoutOp[] = []
    if (resetVessels) ops.push({ op: "reset_vessels" })
    if (resetPresets) {
      ops.push({ op: "reset_presets" })
      return ops // reset_presets cannot combine with per-loadout edits
    }
    for (const [idx, name] of Object.entries(renames)) {
      ops.push({ op: "rename", index: Number(idx), name })
    }
    for (const idx of deletes) ops.push({ op: "delete", index: idx })
    return ops
  }

  function onPickFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (file) {
      rememberSaveFile(file)
      setFileTick((t) => t + 1)
    }
  }

  async function doExport() {
    const file = getSaveFile()
    if (!file) return
    setBusy(true)
    try {
      const result = await exportModifiedLoadouts({
        file,
        slotIndex,
        operations: buildOps(),
      })
      setConfirmOpen(false)
      clearAll()
      const parts: string[] = []
      if (result.renamed) parts.push(`${result.renamed} renamed`)
      if (result.deleted) parts.push(`${result.deleted} deleted`)
      if (result.vesselsReset) parts.push("vessels reset")
      if (result.presetsReset) parts.push("all loadouts cleared")
      showSuccessToast(
        `Saved ${result.filename} — ${parts.join(", ")}. Load it in-game, then re-import here to refresh.`,
      )
    } catch (err) {
      showErrorToast(
        err instanceof LoadoutExportError || err instanceof Error
          ? err.message
          : "Export failed",
      )
    } finally {
      setBusy(false)
    }
  }

  if (loadouts.length === 0) {
    return (
      <div className="space-y-4">
        <GlobalControls
          used={0}
          resetVessels={resetVessels}
          setResetVessels={setResetVessels}
          resetPresets={resetPresets}
          setResetPresets={setResetPresets}
          hasLoadouts={false}
        />
        <p className="text-muted-foreground py-8 text-center">
          This character has no saved relic loadouts. Optimize a build and use{" "}
          <strong>Save as loadout</strong> to create one.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <GlobalControls
        used={used}
        resetVessels={resetVessels}
        setResetVessels={setResetVessels}
        resetPresets={resetPresets}
        setResetPresets={setResetPresets}
        hasLoadouts={true}
      />

      {grouped.map(([character, list]) => (
        <div key={character} className="space-y-2">
          <h3 className="text-sm font-semibold text-muted-foreground">
            {character} — {list.length} loadout{list.length !== 1 ? "s" : ""}
          </h3>
          <div className="grid gap-2">
            {list.map((l) => {
              const pendingDelete = deletes.has(l.index)
              const pendingName = renames[l.index]
              return (
                <div
                  key={l.index}
                  className={`rounded-md border p-3 ${
                    pendingDelete
                      ? "border-destructive/50 bg-destructive/5 opacity-60"
                      : resetPresets
                        ? "opacity-50"
                        : ""
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="font-medium">
                        {pendingName ??
                          (l.name || (
                            <span className="text-muted-foreground italic">
                              (unnamed)
                            </span>
                          ))}
                        {pendingName && (
                          <Badge variant="outline" className="ml-2">
                            renamed
                          </Badge>
                        )}
                      </div>
                      <div className="text-xs text-muted-foreground mt-0.5">
                        {l.vessel_name}
                      </div>
                    </div>
                    <div className="flex shrink-0 gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={resetPresets}
                        onClick={() => openRename(l)}
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={resetPresets}
                        onClick={() => toggleDelete(l.index)}
                      >
                        <Trash2
                          className={`h-3.5 w-3.5 ${pendingDelete ? "text-destructive" : ""}`}
                        />
                      </Button>
                    </div>
                  </div>
                  <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
                    {(l.ga_handles ?? []).map((h, i) => {
                      const r = relicByHandle.get(h)
                      return (
                        <div
                          key={`${l.index}-${i}`}
                          className="text-xs truncate"
                        >
                          {h === 0 || !r ? (
                            <span className="text-muted-foreground/50">
                              {h === 0 ? "— empty —" : "(unknown relic)"}
                            </span>
                          ) : (
                            <span
                              style={{ color: COLOR_HEX[r.color] ?? "#aaa" }}
                              title={r.name}
                            >
                              {r.name}
                            </span>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      ))}

      {/* Action bar */}
      {hasChanges && (
        <div className="sticky bottom-2 flex flex-wrap items-center justify-between gap-3 rounded-md border bg-background/95 p-3 shadow">
          <div className="text-sm">
            {[
              renameCount && `${renameCount} renamed`,
              deleteCount && `${deleteCount} to delete`,
              resetVessels && "reset all vessels",
              resetPresets && "clear all loadouts",
            ]
              .filter(Boolean)
              .join(" · ")}
          </div>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={clearAll}>
              Clear
            </Button>
            <Button size="sm" onClick={() => setConfirmOpen(true)}>
              <Download className="mr-1.5 h-4 w-4" />
              Export modified save
            </Button>
          </div>
        </div>
      )}

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
            <Button onClick={saveRename}>Apply</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Export confirmation */}
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Export modified save</DialogTitle>
            <DialogDescription asChild>
              <div className="space-y-2 pt-1">
                <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-amber-700 dark:text-amber-400">
                  <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
                  <span>
                    Back up your original save first. This downloads a modified
                    copy — close the game, then replace your save with it.
                  </span>
                </div>
                <ul className="text-sm list-disc pl-5">
                  {renameCount > 0 && <li>Rename {renameCount} loadout(s).</li>}
                  {deleteCount > 0 && <li>Delete {deleteCount} loadout(s).</li>}
                  {resetVessels && (
                    <li>Unequip all relics from all vessels.</li>
                  )}
                  {resetPresets && <li>Delete ALL saved loadouts.</li>}
                </ul>
              </div>
            </DialogDescription>
          </DialogHeader>

          {!saveFile && (
            <div className="space-y-1 text-sm">
              <p className="text-muted-foreground">
                {saveMeta
                  ? `Re-select your save file (${saveMeta.name}) to export — it must be the same save the loadouts were loaded from.`
                  : "Select your save file (.sl2) to export."}
              </p>
              <input
                type="file"
                accept=".sl2"
                onChange={onPickFile}
                className="text-sm"
              />
            </div>
          )}

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setConfirmOpen(false)}
              disabled={busy}
            >
              Cancel
            </Button>
            <Button onClick={doExport} disabled={busy || !saveFile}>
              {busy ? "Exporting…" : "Download modified save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function GlobalControls({
  used,
  resetVessels,
  setResetVessels,
  resetPresets,
  setResetPresets,
  hasLoadouts,
}: {
  used: number
  resetVessels: boolean
  setResetVessels: (v: boolean) => void
  resetPresets: boolean
  setResetPresets: (v: boolean) => void
  hasLoadouts: boolean
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <Badge variant="secondary">
        {used} / {CAPACITY} loadouts used
      </Badge>
      <div className="flex flex-wrap gap-2">
        <Button
          variant={resetVessels ? "destructive" : "outline"}
          size="sm"
          onClick={() => setResetVessels(!resetVessels)}
        >
          <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
          {resetVessels ? "Reset vessels (pending)" : "Reset all vessels"}
        </Button>
        {hasLoadouts && (
          <Button
            variant={resetPresets ? "destructive" : "outline"}
            size="sm"
            onClick={() => setResetPresets(!resetPresets)}
          >
            <Trash2 className="mr-1.5 h-3.5 w-3.5" />
            {resetPresets ? "Clear all (pending)" : "Reset all loadouts"}
          </Button>
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
}: {
  profileId: string
  slotIndex: number
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
  const [selectedId, setSelectedId] = useState<string | null>(
    profiles.data?.[0]?.id ?? null,
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
        />
      </Suspense>
    </div>
  )
}

// --- anonymous --------------------------------------------------------------

function AnonLoadouts() {
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

  if (allProfiles.length === 0) return <NoProfiles />

  const profile =
    allProfiles.find((c) => c.slot_index === selectedSlot) ?? allProfiles[0]
  const relicByHandle = relicMapFromList(profile?.relics ?? [])
  const loadouts = (profile?.presets ?? []) as ParsedLoadoutData[]

  const handleChange = (slotStr: string) => {
    const slot = Number(slotStr)
    setSelectedSlot(slot)
    const picked = allProfiles.find((c) => c.slot_index === slot)
    if (picked)
      sessionStorage.setItem("selectedProfile", JSON.stringify(picked))
  }

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
        loadouts={loadouts}
        relicByHandle={relicByHandle}
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
    <p className="text-muted-foreground py-8 text-center">
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
        <h1 className="text-2xl font-semibold">Relic Loadouts</h1>
        <p className="text-muted-foreground mt-1">
          View, rename, and delete the in-game relic loadout presets saved in
          your character's save file. Create new ones from the optimizer with{" "}
          <strong>Save as loadout</strong>.
        </p>
      </div>
      <Suspense fallback={<Skeleton className="h-48 w-full" />}>
        {user ? <AuthLoadouts /> : <AnonLoadouts />}
      </Suspense>
    </div>
  )
}
