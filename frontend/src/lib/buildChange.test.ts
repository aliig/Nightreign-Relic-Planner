/**
 * Unit tests for describeBuildChange — the single source of truth for the
 * "what changed since your last save" wording (replacing the opaque "+91 pts").
 * Covers the verdict + relative %, how departed relics are split between "left
 * your save" and "left the layout", and the edge-case fallbacks.
 */
import { describe, expect, it } from "vitest"

import type { BuildChange, RelicRef } from "@/client"
import {
  type ChangeDescription,
  changeSummaryText,
  describeBuildChange,
  rawScoreTooltip,
  relicNames,
} from "./buildChange"

function relic(
  name: string,
  real_id = 1,
  still_owned: boolean | null = null,
): RelicRef {
  return { real_id, name, color: "Red", effects: [], curses: [], still_owned }
}

function change(overrides: Partial<BuildChange>): BuildChange {
  return { status: "improved", ...overrides }
}

/** The relics of one group, by label — the shape the UI renders. */
function groupNames(d: ChangeDescription | null, label: string): string[] {
  const g = d?.groups.find((x) => x.label === label)
  return (g?.relics ?? []).map((r) => r.name ?? "")
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
        entered: [relic("Crimson Whetblade"), relic("Stalwart Horn", 2)],
      }),
    )
    expect(d).not.toBeNull()
    expect(d?.tone).toBe("up")
    // 91/387 ≈ 23.5% -> 24%
    expect(d?.headline).toBe("24% stronger")
    expect(groupNames(d, "Now uses")).toEqual([
      "Crimson Whetblade",
      "Stalwart Horn",
    ])
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

  it("improved: displaced relics stay quiet unless they left the save", () => {
    const d = describeBuildChange(
      change({
        status: "improved",
        best_before: 100,
        best_after: 150,
        entered: [relic("New Thing", 9)],
        left: [relic("Benched", 1, true), relic("Sold", 2, false)],
      }),
    )
    expect(groupNames(d, "No longer in your save")).toEqual(["Sold"])
    expect(d?.groups.some((g) => g.kind === "benched")).toBe(false)
  })

  it("degraded: weaker % + relics split by whether they left the save", () => {
    const d = describeBuildChange(
      change({
        status: "degraded",
        best_before: 400,
        best_after: 352,
        delta: -48,
        left: [relic("Stalwart Horn", 1, false), relic("Kept Relic", 2, true)],
      }),
    )
    expect(d?.tone).toBe("down")
    expect(d?.headline).toBe("12% weaker")
    expect(groupNames(d, "No longer in your save")).toEqual(["Stalwart Horn"])
    expect(groupNames(d, "No longer used")).toEqual(["Kept Relic"])
    // Something did leave the save, so no "still in your save" note.
    expect(d?.note).toBeUndefined()
  })

  it("degraded: relics still owned are never described as lost", () => {
    const d = describeBuildChange(
      change({
        status: "degraded",
        best_before: 400,
        best_after: 352,
        delta: -48,
        left: [relic("The Will of Balance", 1, true)],
      }),
    )
    expect(d?.groups.map((g) => g.kind)).toEqual(["benched"])
    expect(groupNames(d, "No longer used")).toEqual(["The Will of Balance"])
    expect(d?.note).toBe(
      "still in your save — the best setup just moved on from them",
    )
  })

  it("degraded: an unchecked relic (old snapshot) claims no loss", () => {
    // still_owned=null — the backend never checked, so it must not read as gone.
    const d = describeBuildChange(
      change({
        status: "degraded",
        best_before: 400,
        best_after: 352,
        left: [relic("Unknown Provenance")],
      }),
    )
    expect(groupNames(d, "No longer used")).toEqual(["Unknown Provenance"])
    expect(groupNames(d, "No longer in your save")).toEqual([])
  })

  it("degraded with an unfillable vessel: no %/tooltip, still names the relics", () => {
    const d = describeBuildChange(
      change({
        status: "degraded",
        best_before: 400,
        best_after: null,
        left: [relic("Stalwart Horn", 1, false)],
      }),
    )
    expect(d?.headline).toBe("weaker")
    expect(d?.rawScore).toBeUndefined()
    expect(rawScoreTooltip(d?.rawScore)).toBeUndefined()
    expect(groupNames(d, "No longer in your save")).toEqual(["Stalwart Horn"])
  })

  it("reordered: same strength, swapped relics", () => {
    const d = describeBuildChange(
      change({
        status: "reordered",
        best_before: 300,
        best_after: 300,
        delta: 0,
        entered: [relic("Gilded Greatrune")],
        left: [relic("Old Faithful", 2, true)],
      }),
    )
    expect(d?.tone).toBe("neutral")
    expect(d?.headline).toBe("rearranged, same strength")
    expect(groupNames(d, "Swaps in")).toEqual(["Gilded Greatrune"])
    expect(groupNames(d, "Swaps out")).toEqual(["Old Faithful"])
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
    expect(groupNames(d, "Pin lost")).toEqual(["Pinned Wonder"])
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

  it("keeps every relic in the group (the UI caps what it shows)", () => {
    const d = describeBuildChange(
      change({
        status: "improved",
        best_before: 100,
        best_after: 150,
        entered: [relic("A", 1), relic("B", 2), relic("C", 3), relic("D", 4)],
      }),
    )
    expect(groupNames(d, "Now uses")).toEqual(["A", "B", "C", "D"])
    expect(relicNames(d?.groups[0].relics)).toBe("A, B, C, D")
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

  it("changeSummaryText flattens a change for title/aria", () => {
    const d = describeBuildChange(
      change({
        status: "degraded",
        best_before: 400,
        best_after: 352,
        left: [relic("Sold Relic", 1, false)],
        entered: [relic("Stand-in", 2)],
      }),
    )!
    expect(changeSummaryText(d)).toBe(
      "12% weaker — no longer in your save: Sold Relic — now uses: Stand-in",
    )
  })

  it("relicNames is empty when no relics moved", () => {
    expect(relicNames(undefined)).toBe("")
  })
})
