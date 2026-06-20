import { AlertTriangle, CheckCircle2, Loader2 } from "lucide-react"
import { useEffect, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { describeBuildChange, relicSummary } from "@/lib/buildChange"
import {
  type BuildJobInfo,
  dismissJob,
  useOptimizeJob,
} from "@/lib/optimizeJobs"
import { computeOverallPct, optimizingLabel } from "@/lib/optimizeProgress"

/** One row in the Sheet: a build with its live status (and final change, if done). */
function BuildRow({ info }: { info: BuildJobInfo }) {
  const d = info.status === "done" ? describeBuildChange(info.change) : null
  return (
    <li className="flex items-center justify-between gap-2 rounded-md border px-2.5 py-1.5 text-sm">
      <div className="flex min-w-0 items-center gap-2">
        {info.status === "optimizing" && (
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />
        )}
        {info.status === "done" && (
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-green-600 dark:text-green-500" />
        )}
        {info.status === "error" && (
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-destructive" />
        )}
        <div className="min-w-0">
          <div className="truncate">{info.name ?? "Build"}</div>
          {d && (
            <div className={`text-xs ${d.textClass}`}>
              {d.headline}
              {d.relics && (
                <span className="text-muted-foreground">
                  {" "}
                  · {d.relics.verb} {relicSummary(d.relics)}
                </span>
              )}
            </div>
          )}
          {info.status === "error" && (
            <div className="text-xs text-destructive">Optimization failed</div>
          )}
        </div>
      </div>
    </li>
  )
}

/**
 * Navbar indicator for the background save re-optimization (lib/optimizeJobs).
 *
 * Mirrors PendingExportButton: a compact trigger that opens a Sheet. Renders
 * nothing when idle. Because the job lives in a module store, this stays live as
 * the user navigates — start an upload on /upload, watch it finish from anywhere.
 */
/** How long a finished (done) job lingers in the navbar before auto-clearing. */
const AUTO_CLEAR_MS = 5000

export function OptimizeTrackerButton() {
  const job = useOptimizeJob()
  const [open, setOpen] = useState(false)

  // Auto-clear a finished job after a few seconds — but pause while the Sheet is
  // open so it doesn't vanish mid-read. Errors stay until manually dismissed.
  const finished = job?.status === "done"
  useEffect(() => {
    if (!finished || open) return
    const t = setTimeout(() => dismissJob(), AUTO_CLEAR_MS)
    return () => clearTimeout(t)
  }, [finished, open])

  if (!job) return null

  const active = job.status === "parsing" || job.status === "optimizing"
  const builds = Object.entries(job.builds)
  const total = job.progress.buildTotal ?? builds.length
  const doneCount = builds.filter(([, b]) => b.status !== "optimizing").length

  return (
    <>
      <Button
        size="sm"
        variant="outline"
        onClick={() => setOpen(true)}
        className="gap-1.5"
      >
        {active && <Loader2 className="h-4 w-4 animate-spin text-primary" />}
        {job.status === "done" && (
          <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-500" />
        )}
        {job.status === "error" && (
          <AlertTriangle className="h-4 w-4 text-destructive" />
        )}
        {active
          ? "Optimizing"
          : job.status === "error"
            ? "Failed"
            : "Optimized"}
        {active && total > 0 && (
          <Badge variant="secondary" className="ml-1 px-1.5">
            {doneCount}/{total}
          </Badge>
        )}
      </Button>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent className="w-full sm:max-w-md">
          <SheetHeader>
            <SheetTitle>Save optimization</SheetTitle>
            <SheetDescription>
              {active
                ? "Re-optimizing the builds your new save affects. This keeps running while you use the app."
                : job.status === "error"
                  ? "Something went wrong while optimizing your save."
                  : "Done — your builds are up to date with the latest save."}
            </SheetDescription>
          </SheetHeader>

          <div className="flex-1 space-y-4 overflow-auto px-4">
            {job.status === "parsing" && (
              <p className="animate-pulse text-sm text-muted-foreground">
                Parsing save file…
              </p>
            )}

            {job.status === "optimizing" && (
              <div className="space-y-2">
                <p className="text-sm text-muted-foreground">
                  {optimizingLabel(job.progress)}
                </p>
                <Progress value={computeOverallPct(job.progress)} />
              </div>
            )}

            {job.status === "error" && job.error && (
              <p className="text-sm text-destructive">{job.error}</p>
            )}

            {builds.length > 0 && (
              <ul className="space-y-1">
                {builds.map(([id, info]) => (
                  <BuildRow key={id} info={info} />
                ))}
              </ul>
            )}
          </div>

          {!active && (
            <SheetFooter>
              <Button
                variant="outline"
                onClick={() => {
                  dismissJob()
                  setOpen(false)
                }}
              >
                Dismiss
              </Button>
            </SheetFooter>
          )}
        </SheetContent>
      </Sheet>
    </>
  )
}
