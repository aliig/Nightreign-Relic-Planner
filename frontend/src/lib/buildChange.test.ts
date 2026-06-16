/**
 * Unit tests for describeBuildChange — the single source of truth for the
 * "what changed since your last save" wording (replacing the opaque "+91 pts").
 * Covers the verdict + relative %, the relic detail, and the edge-case fallbacks.
 */
import { describe, expect, it } from "vitest"

import type { BuildChange, RelicRef } from "@/client"
import {
  describeBuildChange,
  rawScoreTooltip,
  relicSummary,
} from "./buildChange"

function relic(name: string, real_id = 1): RelicRef {
  return { real_id, name, color: "Red", effects: [], curses: [] }
}

function change(overrides: Partial<BuildChange>): BuildChange {
  return { status: "improved", ...overrides }
}

describe("describeBuildChange", () => {
  it("returns null for nothing to show", () => {
    expect(describeBuildChange(null)).toBeNull()
    expect(describeBuildChange(undefined)).toBeNull()
    expect(describeBuildChange(change({ status: "unchanged" }))).toBeNull()
    expect(describeBuildChange(change({ status: "new" }))).toBeNull()
  })

  it("improved: relative % verdict + entered relics + raw tooltip", () => {
    const d = describeBuildChange(
      change({
        status: "improved",
        best_before: 387,
        best_after: 478,
        delta: 91,
        entered: [relic("Crimson Whetblade"), relic("Stalwart Horn")],
      }),
    )
    expect(d).not.toBeNull()
    expect(d?.tone).toBe("up")
    // 91/387 ≈ 23.5% -> 24%
    expect(d?.headline).toBe("24% stronger")
    expect(d?.relics).toEqual({
      verb: "now uses",
      names: ["Crimson Whetblade", "Stalwart Horn"],
      extra: 0,
    })
    expect(rawScoreTooltip(d?.rawScore)).toBe("387 → 478 pts")
  })

  it("improved from a zero baseline reads as 'newly viable'", () => {
    const d = describeBuildChange(
      change({
        status: "improved",
        best_before: 0,
        best_after: 120,
        delta: 120,
      }),
    )
    expect(d?.headline).toBe("newly viable")
  })

  it("degraded: weaker % + lost relics", () => {
    const d = describeBuildChange(
      change({
        status: "degraded",
        best_before: 400,
        best_after: 352,
        delta: -48,
        left: [relic("Stalwart Horn")],
      }),
    )
    expect(d?.tone).toBe("down")
    expect(d?.headline).toBe("12% weaker")
    expect(d?.relics?.verb).toBe("lost")
    expect(d?.relics?.names).toEqual(["Stalwart Horn"])
  })

  it("degraded with an unfillable vessel: no %/tooltip, still names what was lost", () => {
    const d = describeBuildChange(
      change({
        status: "degraded",
        best_before: 400,
        best_after: null,
        left: [relic("Stalwart Horn")],
      }),
    )
    expect(d?.headline).toBe("weaker")
    expect(d?.rawScore).toBeUndefined()
    expect(rawScoreTooltip(d?.rawScore)).toBeUndefined()
  })

  it("reordered: same strength, swapped relics", () => {
    const d = describeBuildChange(
      change({
        status: "reordered",
        best_before: 300,
        best_after: 300,
        delta: 0,
        entered: [relic("Gilded Greatrune")],
      }),
    )
    expect(d?.tone).toBe("neutral")
    expect(d?.headline).toBe("rearranged, same strength")
    expect(d?.relics).toEqual({
      verb: "swaps in",
      names: ["Gilded Greatrune"],
      extra: 0,
    })
  })

  it("broken_pin: warn tone naming the lost pin", () => {
    const d = describeBuildChange(
      change({
        status: "broken_pin",
        pinned_removed: [relic("Pinned Wonder")],
      }),
    )
    expect(d?.tone).toBe("warn")
    expect(d?.headline).toBe("a pinned relic left your save")
    expect(d?.relics?.verb).toBe("pin lost")
  })

  it("potentially_affected: pluralizes the relevant-relic hint", () => {
    expect(
      describeBuildChange(
        change({ status: "potentially_affected", relevant_added: 2 }),
      )?.headline,
    ).toBe("2 new relics may help")
    expect(
      describeBuildChange(
        change({ status: "potentially_affected", relevant_added: 1 }),
      )?.headline,
    ).toBe("1 new relic may help")
    expect(
      describeBuildChange(
        change({ status: "potentially_affected", relevant_added: 0 }),
      )?.headline,
    ).toBe("new relics may help")
  })

  it("caps the relic list at two names + an overflow count", () => {
    const d = describeBuildChange(
      change({
        status: "improved",
        best_before: 100,
        best_after: 150,
        entered: [relic("A"), relic("B"), relic("C"), relic("D")],
      }),
    )
    expect(d?.relics).toEqual({ verb: "now uses", names: ["A", "B"], extra: 2 })
    expect(relicSummary(d?.relics)).toBe("A, B +2 more")
  })

  it("propagates an unreliable (truncated-search) delta", () => {
    const d = describeBuildChange(
      change({
        status: "improved",
        best_before: 100,
        best_after: 150,
        reliable: false,
      }),
    )
    expect(d?.reliable).toBe(false)
  })

  it("relicSummary is empty when no relics moved", () => {
    expect(relicSummary(undefined)).toBe("")
  })
})
