import { AlertTriangle, Download, ListChecks, Undo2 } from "lucide-react"
import type { ReactNode } from "react"
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import useCustomToast from "@/hooks/useCustomToast"
import { exportPendingChanges, PendingExportError } from "@/lib/exportPending"
import {
  clearAll,
  type PendingLoadoutOp,
  readAll,
  removeLoadoutOp,
  setFavorite,
  toggleSell,
  usePendingAll,
} from "@/lib/pendingChanges"
import { downloadOriginalBackup, useOriginalBackup } from "@/lib/saveBackup"
import { getSaveFile, getSaveFileMeta, rememberSaveFile } from "@/lib/saveFile"
import { formatMurks } from "@/lib/sellValue"

function describeOp(op: PendingLoadoutOp): string {
  switch (op.kind) {
    case "add":
      return `Add loadout "${op.name}" (${op.character})`
    case "overwrite":
      return `Replace "${op.targetName || "loadout"}" with a new setup`
    case "delete":
      return `Delete loadout "${op.name}"`
    case "rename":
      return `Rename "${op.oldName}" → "${op.name}"`
    case "reset_vessels":
      return "Reset all vessels (unequip every relic)"
    case "reset_presets":
      return "Delete ALL loadouts"
  }
}

type ChangeEntry = {
  id: string
  label: ReactNode
  sub?: string
  warn?: string
  undo: () => void
}

export function PendingExportButton() {
  const state = usePendingAll()
  const { meta: backupMeta } = useOriginalBackup()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [, setFileTick] = useState(0)

  // Flatten the whole diff into a per-slot change log with undo for each entry.
  const slots = Object.keys(state)
    .map(Number)
    .sort((a, b) => a - b)
  const groups: Array<{ slot: number; entries: ChangeEntry[] }> = []
  let count = 0
  for (const slot of slots) {
    const s = state[slot]
    const entries: ChangeEntry[] = []
    for (const h of s.sells) {
      const m = s.meta[h]
      entries.push({
        id: `sell-${h}`,
        label: `Trash ${m?.name ?? `Relic #${h}`}`,
        sub: m?.murk ? `+${formatMurks(m.murk)} Murk` : undefined,
        warn: m?.builds
          ? `Used in ${m.builds} build${m.builds !== 1 ? "s" : ""} — may change their best result`
          : undefined,
        undo: () => toggleSell(slot, h),
      })
    }
    for (const [hStr, desired] of Object.entries(s.favorites)) {
      const h = Number(hStr)
      const m = s.meta[h]
      const name = m?.name ?? `Relic #${h}`
      entries.push({
        id: `fav-${h}`,
        label: desired ? `Bookmark ${name}` : `Remove bookmark — ${name}`,
        undo: () => setFavorite(slot, h, null),
      })
    }
    for (const op of s.loadoutOps) {
      entries.push({
        id: `op-${op.id}`,
        label: describeOp(op),
        undo: () => removeLoadoutOp(slot, op.id),
      })
    }
    count += entries.length
    if (entries.length) groups.push({ slot, entries })
  }

  if (count === 0) return null

  const saveFile = getSaveFile()
  const saveMeta = getSaveFileMeta()
  const multiSlot = groups.length > 1

  function onPickFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    // Soft save-identity guard: the changes were computed against a specific
    // save, so exporting onto a different file risks a wrong/corrupt result.
    const expected = getSaveFileMeta()
    if (expected && file.name !== expected.name) {
      showErrorToast(
        `Heads up: "${file.name}" doesn't match the save your changes came from ("${expected.name}"). Make sure it's the right save before exporting.`,
      )
    }
    rememberSaveFile(file)
    setFileTick((t) => t + 1)
  }

  async function doExport() {
    const file = getSaveFile()
    if (!file) return
    setBusy(true)
    try {
      const r = await exportPendingChanges(file, readAll())
      clearAll()
      setOpen(false)
      const parts: string[] = []
      if (r.sold) parts.push(`${r.sold} relic(s) sold`)
      if (r.bookmarks) parts.push(`${r.bookmarks} bookmark change(s)`)
      if (r.added) parts.push(`${r.added} loadout(s) added`)
      if (r.deleted) parts.push(`${r.deleted} deleted`)
      if (r.renamed) parts.push(`${r.renamed} renamed`)
      if (r.overwritten) parts.push(`${r.overwritten} replaced`)
      if (r.vesselsReset) parts.push("vessels reset")
      if (r.presetsReset) parts.push("all loadouts cleared")
      showSuccessToast(
        `Saved ${r.filename} — ${parts.join(", ") || "no changes"}. Close the game, replace your save with this file, then re-upload here to re-sync the planner.`,
      )
    } catch (err) {
      showErrorToast(
        err instanceof PendingExportError || err instanceof Error
          ? err.message
          : "Export failed",
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Button
        size="sm"
        variant="outline"
        onClick={() => setOpen(true)}
        className="gap-1.5"
      >
        <ListChecks className="h-4 w-4" />
        Changes
        <Badge variant="secondary" className="ml-1 px-1.5">
          {count}
        </Badge>
      </Button>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent className="w-full sm:max-w-md">
          <SheetHeader>
            <SheetTitle>Your changes</SheetTitle>
            <SheetDescription>
              These edits are already applied in the planner. Export to write
              them to a save file — nothing changes on disk until you do.
            </SheetDescription>
          </SheetHeader>

          <div className="flex-1 space-y-4 overflow-auto px-4">
            {groups.map((g) => (
              <div key={g.slot} className="space-y-1">
                {multiSlot && (
                  <p className="text-xs font-medium text-muted-foreground">
                    Slot {g.slot}
                  </p>
                )}
                <ul className="space-y-1">
                  {g.entries.map((e) => (
                    <li
                      key={e.id}
                      className="flex items-center justify-between gap-2 rounded-md border px-2.5 py-1.5 text-sm"
                    >
                      <div className="min-w-0">
                        <div className="truncate">{e.label}</div>
                        {e.sub && (
                          <div className="text-xs text-green-600 dark:text-green-500">
                            {e.sub}
                          </div>
                        )}
                        {e.warn && (
                          <div className="text-xs text-amber-600 dark:text-amber-500">
                            {e.warn}
                          </div>
                        )}
                      </div>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 shrink-0 gap-1 px-2 text-muted-foreground"
                        onClick={e.undo}
                        title="Undo this change"
                      >
                        <Undo2 className="h-3.5 w-3.5" />
                        Undo
                      </Button>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>

          <SheetFooter>
            <div className="space-y-2">
              <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-sm text-amber-700 dark:text-amber-400">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>
                  Back up your original save first. Export downloads a modified
                  copy — close the game, then replace your save with it.
                </span>
              </div>
              {backupMeta && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void downloadOriginalBackup()}
                  className="w-full gap-1.5"
                >
                  <Download className="h-4 w-4" />
                  Download a backup of your original ({backupMeta.name})
                </Button>
              )}
            </div>

            {!saveFile && (
              <div className="space-y-1 text-sm">
                <p className="text-muted-foreground">
                  {saveMeta
                    ? `Re-select your save file (${saveMeta.name}) to export — it must be the same save these changes came from.`
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

            <div className="flex items-center justify-between gap-2">
              <Button
                variant="ghost"
                onClick={() => {
                  clearAll()
                  setOpen(false)
                }}
                disabled={busy}
              >
                Discard all
              </Button>
              <Button
                onClick={doExport}
                disabled={busy || !saveFile}
                className="gap-1.5"
              >
                <Download className="h-4 w-4" />
                {busy ? "Exporting…" : "Export save"}
              </Button>
            </div>
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </>
  )
}
