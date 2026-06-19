import { Download, ShieldCheck, Trash2 } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  clearOriginalBackup,
  downloadOriginalBackup,
  useOriginalBackup,
} from "@/lib/saveBackup"
import { formatRelativeTime } from "@/utils"

/**
 * Surfaces the durable, in-browser backup of the user's original imported save:
 * one-click download (recovery) and remove. Renders nothing when there's no
 * backup (or IndexedDB is unavailable).
 */
export function OriginalBackupCard() {
  const { meta } = useOriginalBackup()
  if (!meta) return null
  return (
    <Alert>
      <ShieldCheck className="h-4 w-4" />
      <AlertTitle>Your original save is backed up</AlertTitle>
      <AlertDescription>
        <p className="mt-1 text-xs">
          Kept safely in this browser (never uploaded) — your recovery net if a
          save ever gets corrupted.{" "}
          <span className="font-medium">{meta.name}</span> · imported{" "}
          {formatRelativeTime(meta.importedAt)}.
        </p>
        <div className="mt-2 flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => void downloadOriginalBackup()}
            className="gap-1.5"
          >
            <Download className="h-3.5 w-3.5" />
            Download original
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void clearOriginalBackup()}
            className="gap-1.5 text-muted-foreground"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Remove backup
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  )
}
