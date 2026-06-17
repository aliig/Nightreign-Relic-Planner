/**
 * Pure helpers for the post-upload re-optimization progress indicator.
 *
 * The backend streams two granularities while re-optimizing the builds a new
 * save affects: per-build markers (`optimize_start` — which build, of how many)
 * and per-vessel ticks within each build (`optimize_progress`). Folding them
 * into ONE overall fraction yields a bar that only ever moves forward across the
 * whole job, instead of resetting to 0 at every build boundary.
 */

/** The fields these helpers read — a structural subset of upload.tsx's
 *  StreamUploadProgress, so the live progress object can be passed directly. */
export interface OptimizeBuildProgress {
  /** 1-based index of the build currently being optimized. */
  buildIndex?: number
  /** Total number of builds this save affects. */
  buildTotal?: number
  buildName?: string
  /** Vessels completed within the current build. */
  vessel?: number
  /** Total vessels in the current build. */
  vesselTotal?: number
}

/**
 * Overall completion percentage in [0, 100] across ALL affected builds.
 *
 * Each build contributes an equal 1/buildTotal slice; within the current build
 * the vessel fraction fills that slice. Monotonic by construction: between a
 * build's last vessel and the next build's first the vessel fraction is 0, so
 * the value holds steady at buildIndex/buildTotal rather than dropping back.
 * Returns 0 until the first build's total is known.
 */
export function computeOverallPct(p: OptimizeBuildProgress): number {
  const { buildIndex, buildTotal, vessel, vesselTotal } = p
  if (!buildTotal || buildTotal <= 0) return 0
  const vesselFrac = vessel && vesselTotal ? vessel / vesselTotal : 0
  const completedBuilds = (buildIndex ?? 0) - 1
  const pct = ((completedBuilds + vesselFrac) / buildTotal) * 100
  return Math.max(0, Math.min(100, pct))
}

/** Plain-language status line for the optimizing phase. */
export function optimizingLabel(p: OptimizeBuildProgress): string {
  const { buildIndex, buildTotal, buildName } = p
  if (buildTotal && buildTotal > 1 && buildIndex) {
    const named = buildName ? ` — "${buildName}"` : ""
    return `Optimizing build ${buildIndex} of ${buildTotal}${named}…`
  }
  if (buildName) return `Optimizing "${buildName}"…`
  return "Optimizing builds…"
}
