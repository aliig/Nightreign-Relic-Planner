import { AlertTriangle, Trash2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import type { SlotSummary } from "@/lib/pendingChanges"
import { formatMurks } from "@/lib/sellValue"

function summaryLine(row: SlotSummary): string {
  const parts: string[] = []
  if (row.sells)
    parts.push(
      `${row.sells} trashed relic${row.sells !== 1 ? "s" : ""}` +
        (row.sellRefund ? ` (+${formatMurks(row.sellRefund)} Murk)` : ""),
    )
  if (row.mints)
    parts.push(
      `${row.mints} purchased relic${row.mints !== 1 ? "s" : ""}` +
        (row.murkDelta
          ? ` (${row.murkDelta < 0 ? "−" : "+"}${formatMurks(Math.abs(row.murkDelta))} Murk)`
          : ""),
    )
  // An all-dud rites batch: no relics kept, but the buy/sell spread is a real
  // staged Murk spend the discard would drop.
  else if (row.murkDelta)
    parts.push(
      `${row.murkDelta < 0 ? "−" : "+"}${formatMurks(Math.abs(row.murkDelta))} Murk from purchases (all sold back)`,
    )
  if (row.favorites)
    parts.push(
      `${row.favorites} bookmark change${row.favorites !== 1 ? "s" : ""}`,
    )
  if (row.loadoutOps)
    parts.push(
      `${row.loadoutOps} loadout edit${row.loadoutOps !== 1 ? "s" : ""}`,
    )
  return parts.join(", ")
}

/**
 * Confirmation shown when a new save upload would discard staged edits. Edits
 * are keyed to the save they were made against, so a replacement file can't
 * inherit them — the only choices are to knowingly discard, or cancel and
 * export from the Changes panel first.
 */
export function UploadGateDialog({
  open,
  summaries,
  hasMints,
  onDiscard,
  onCancel,
}: {
  open: boolean
  summaries: SlotSummary[]
  hasMints: boolean
  onDiscard: () => void
  onCancel: () => void
}) {
  return (
    <Dialog open={open} onOpenChange={(v) => !v && onCancel()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-amber-500" />
            Uploading will discard your staged changes
          </DialogTitle>
          <DialogDescription>
            These edits belong to the save that's currently loaded and can't
            carry over to a different file. Are you sure?
          </DialogDescription>
        </DialogHeader>

        <ul className="space-y-1 text-sm">
          {summaries.map((row) => (
            <li key={row.slot} className="rounded-md border px-2.5 py-1.5">
              <span className="font-medium">Slot {row.slot}:</span>{" "}
              {summaryLine(row)}
            </li>
          ))}
        </ul>

        <p className="text-xs text-muted-foreground">
          To keep them, cancel and export your changes from the Changes panel
          first.
          {hasMints &&
            " Purchased relics can't carry over either way — their rolls belong to the save they were purchased from — but you can re-run Relic Rites against the new save after uploading."}
        </p>

        <DialogFooter className="flex-col gap-2 sm:flex-col">
          <Button
            variant="destructive"
            onClick={onDiscard}
            className="w-full gap-1.5"
          >
            <Trash2 className="h-4 w-4" />
            Discard changes and upload
          </Button>
          <Button variant="ghost" onClick={onCancel} className="w-full">
            Cancel upload
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
