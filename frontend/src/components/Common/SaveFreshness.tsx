import { History } from "lucide-react"

import { useSaveStatus } from "@/hooks/useSaveStatus"
import { cn } from "@/lib/utils"
import { formatRelativeTime } from "@/utils"

/**
 * Small muted "Updated <relative time>" line for the save-file-backed views
 * (inventory, game loadouts). Reads the most recent save upload time so the user
 * can see how fresh the data on screen is. Works for both signed-in users
 * (server save-status) and anonymous sessions (sessionStorage) via useSaveStatus.
 * Renders nothing until a save has been loaded. Hovering shows the exact local
 * timestamp.
 */
export function SaveFreshness({ className }: { className?: string }) {
  const { status } = useSaveStatus()
  if (!status?.uploaded_at) return null

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 text-xs text-muted-foreground",
        className,
      )}
      title={new Date(status.uploaded_at).toLocaleString()}
    >
      <History className="h-3 w-3" />
      Updated {formatRelativeTime(status.uploaded_at)}
    </span>
  )
}
