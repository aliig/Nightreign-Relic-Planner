import { AlertTriangle, Download } from "lucide-react"
import { useState } from "react"

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
import useCustomToast from "@/hooks/useCustomToast"
import { exportPendingChanges, PendingExportError } from "@/lib/exportPending"
import {
  clearAll,
  type PendingLoadoutOp,
  readAll,
  usePendingTotal,
} from "@/lib/pendingChanges"
import { getSaveFile, getSaveFileMeta, rememberSaveFile } from "@/lib/saveFile"

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

export function PendingExportButton() {
  const { count } = usePendingTotal()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [, setFileTick] = useState(0)

  if (count === 0) return null

  const saveFile = getSaveFile()
  const saveMeta = getSaveFileMeta()
  const pending = readAll()
  const lines: string[] = []
  let sells = 0
  let bookmarks = 0
  for (const slot of Object.values(pending)) {
    sells += slot.sells.length
    bookmarks += Object.keys(slot.favorites).length
    for (const op of slot.loadoutOps) lines.push(describeOp(op))
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
        `Saved ${r.filename} — ${parts.join(", ") || "no changes"}. Load it in-game, then re-import here to refresh.`,
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
      <Button size="sm" onClick={() => setOpen(true)} className="gap-1.5">
        <Download className="h-4 w-4" />
        Export save
        <Badge variant="secondary" className="ml-1 px-1.5">
          {count}
        </Badge>
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
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
              </div>
            </DialogDescription>
          </DialogHeader>

          <div className="max-h-64 space-y-1 overflow-auto text-sm">
            {sells > 0 && <div>Sell {sells} relic(s)</div>}
            {bookmarks > 0 && <div>{bookmarks} bookmark change(s)</div>}
            {lines.map((l) => (
              <div key={l}>{l}</div>
            ))}
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

          <DialogFooter className="gap-2 sm:justify-between">
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
            <div className="flex gap-2">
              <Button
                variant="outline"
                onClick={() => setOpen(false)}
                disabled={busy}
              >
                Cancel
              </Button>
              <Button onClick={doExport} disabled={busy || !saveFile}>
                {busy ? "Exporting…" : "Download modified save"}
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
