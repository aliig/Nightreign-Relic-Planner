import { Loader2, RefreshCw } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { useProfileStaged } from "@/hooks/useStaleBuilds"
import { startOptimizeAll, useOptimizeJob } from "@/lib/optimizeJobs"

/**
 * "Your builds are out of date — bring them current."
 *
 * The app re-optimizes on two triggers of its own: uploading a save, and
 * viewing a single build. Neither covers the case that sent the user here —
 * buying relics in Relic Rites changes the inventory EVERY build is scored
 * against, so every build's verdict on this page silently goes stale at once.
 * This is the trigger for that, and it stays visible when nothing is stale so
 * a re-run is always one click away.
 */
export function OptimizeAllBar({
  builds,
  stale,
  known,
  optimized,
  profileId: pickedProfileId,
  emptyLabel,
  staleDetail,
}: {
  builds: { id: string; name: string }[]
  stale: Set<string>
  /** Whether the freshness answer has arrived. Until it has, the page must not
   *  claim the builds are current — it does not know yet. */
  known: boolean
  /** Builds that have results at all. A build that has never been run is also
   *  "not fresh", but telling the user their relics changed would be a lie —
   *  nothing has changed, there is simply nothing to compare against yet. */
  optimized: Set<string>
  /** Which save to optimize against. Omit to use the first profile, as the
   *  builds page does — the inventory page has a picker and passes it. */
  profileId?: string | null
  /** Wording for the nothing-is-stale state; pages phrase it differently. */
  emptyLabel?: string
  /** Replaces the "what being out of date costs you" line. The consequence is
   *  page-specific: on /builds it is wrong verdicts, on /inventory it is
   *  keepers reading as disposable. */
  staleDetail?: string
}) {
  const { profileId, staged } = useProfileStaged(pickedProfileId)
  const job = useOptimizeJob()
  const running =
    job?.status === "parsing" || job?.status === "optimizing" ? job : null

  const staleBuilds = builds.filter((b) => stale.has(b.id))
  const count = staleBuilds.length

  if (running) {
    const total = running.progress.buildTotal ?? 0
    const done = Object.values(running.builds).filter(
      (b) => b.status !== "optimizing",
    ).length
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin text-primary" />
        <span>
          Optimizing{total > 0 ? ` ${done} of ${total}` : ""}… watch progress in
          the top bar.
        </span>
      </div>
    )
  }

  // Optimizing needs a save to score against; without one there is nothing to
  // be out of date with.
  if (!profileId) return null

  const targets = count > 0 ? staleBuilds : builds
  const start = () => startOptimizeAll({ profileId, builds: targets, staged })

  if (count === 0) {
    return (
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          {known
            ? (emptyLabel ??
              "All builds are up to date with your current relics.")
            : ""}
        </p>
        <Button variant="ghost" size="sm" onClick={start} className="gap-1.5">
          <RefreshCw className="h-3.5 w-3.5" />
          Re-optimize all
        </Button>
      </div>
    )
  }

  const neverRun = staleBuilds.filter((b) => !optimized.has(b.id)).length
  const plural = count !== 1 ? "s" : ""
  const [headline, defaultDetail] =
    neverRun === count
      ? [
          `${count} build${plural} not optimized yet`,
          "Run them to see the best relics you own for each.",
        ]
      : neverRun === 0
        ? [
            `${count} build${plural} out of date`,
            "Your relics have changed since these were last optimized — " +
              "their results and verdicts below are from before.",
          ]
        : [
            `${count} build${plural} need optimizing`,
            "Your relics have changed since some were last optimized, and " +
              "others have never been run.",
          ]

  return (
    <Card className="flex flex-row items-center justify-between gap-3 px-4 py-3">
      <div className="min-w-0">
        <p className="text-sm font-medium">{headline}</p>
        <p className="text-xs text-muted-foreground">
          {neverRun === count ? defaultDetail : (staleDetail ?? defaultDetail)}
        </p>
      </div>
      <Button size="sm" onClick={start} className="shrink-0 gap-1.5">
        <RefreshCw className="h-3.5 w-3.5" />
        Optimize {count} build{plural}
      </Button>
    </Card>
  )
}
