/**
 * Unit tests for the post-upload re-optimization progress helpers.
 *
 * The key regression: the bar must NEVER reset to 0 between builds. Earlier it
 * was driven by per-build vessel fraction, so it emptied and refilled at every
 * build boundary. computeOverallPct folds builds + vessels into one monotonic
 * fraction; these tests pin that down.
 */
import { describe, expect, it } from "vitest"

import {
  computeOverallPct,
  type OptimizeBuildProgress,
  optimizingLabel,
} from "./optimizeProgress"

/** Replays the merge logic from runUploadStream to produce the sequence of
 *  progress snapshots the bar actually sees, given each build's vessel count. */
function replay(buildVesselCounts: number[]): OptimizeBuildProgress[] {
  const buildTotal = buildVesselCounts.length
  const snapshots: OptimizeBuildProgress[] = []
  let progress: OptimizeBuildProgress = {}
  const emit = (patch: Partial<OptimizeBuildProgress>) => {
    progress = { ...progress, ...patch }
    snapshots.push(progress)
  }
  buildVesselCounts.forEach((vesselTotal, i) => {
    // optimize_start — build identity set, vessel sub-progress cleared
    emit({
      buildIndex: i + 1,
      buildTotal,
      buildName: `Build ${i + 1}`,
      vessel: undefined,
      vesselTotal: undefined,
    })
    // optimize_progress — one tick per completed vessel
    for (let v = 1; v <= vesselTotal; v++) {
      emit({ vessel: v, vesselTotal })
    }
  })
  return snapshots
}

describe("computeOverallPct", () => {
  it("never goes backwards across build boundaries and ends at 100", () => {
    const pcts = replay([12, 3, 8, 1, 5]).map(computeOverallPct)

    for (let i = 1; i < pcts.length; i++) {
      expect(pcts[i]).toBeGreaterThanOrEqual(pcts[i - 1])
    }
    expect(pcts[0]).toBe(0) // first event is optimize_start, no vessel yet
    expect(pcts[pcts.length - 1]).toBe(100)
  })

  it("fills 0→100 for a single affected build", () => {
    const pcts = replay([4]).map(computeOverallPct)
    expect(pcts).toEqual([0, 25, 50, 75, 100])
  })

  it("returns 0 until the first build's total is known", () => {
    expect(computeOverallPct({})).toBe(0)
    expect(computeOverallPct({ buildTotal: 0 })).toBe(0)
    expect(computeOverallPct({ buildName: "x" })).toBe(0)
  })

  it("fills each build's equal slice by its vessel fraction", () => {
    // build 2 of 4, 6/12 vessels → (1 + 0.5) / 4 = 37.5%
    expect(
      computeOverallPct({
        buildIndex: 2,
        buildTotal: 4,
        vessel: 6,
        vesselTotal: 12,
      }),
    ).toBe(37.5)
  })

  it("holds steady at a build boundary when the vessel count resets", () => {
    // last vessel of build 1 of 4 …
    const endOfBuild1 = computeOverallPct({
      buildIndex: 1,
      buildTotal: 4,
      vessel: 12,
      vesselTotal: 12,
    })
    // … then optimize_start of build 2 (vessel cleared) — same value, not lower
    const startOfBuild2 = computeOverallPct({ buildIndex: 2, buildTotal: 4 })
    expect(endOfBuild1).toBe(25)
    expect(startOfBuild2).toBe(25)
  })

  it("clamps to [0, 100]", () => {
    expect(
      computeOverallPct({
        buildIndex: 9,
        buildTotal: 5,
        vessel: 12,
        vesselTotal: 12,
      }),
    ).toBe(100)
    expect(computeOverallPct({ buildIndex: 0, buildTotal: 5 })).toBe(0)
  })
})

describe("optimizingLabel", () => {
  it("multi-build: shows build X of N with the name", () => {
    expect(
      optimizingLabel({
        buildIndex: 2,
        buildTotal: 5,
        buildName: "Crimson Aegis",
      }),
    ).toBe('Optimizing build 2 of 5 — "Crimson Aegis"…')
  })

  it("multi-build: omits the name when not yet known", () => {
    expect(optimizingLabel({ buildIndex: 2, buildTotal: 5 })).toBe(
      "Optimizing build 2 of 5…",
    )
  })

  it("single build: just the name", () => {
    expect(
      optimizingLabel({ buildIndex: 1, buildTotal: 1, buildName: "Solo" }),
    ).toBe('Optimizing "Solo"…')
  })

  it("falls back before the first build starts", () => {
    expect(optimizingLabel({})).toBe("Optimizing builds…")
  })
})
