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
  changeCauses,
  changeSummaryText,
  describeBuildChange,
  isChangeNews,
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

  it("reordered: suppressed — a same-strength shuffle is not news", () => {
    expect(
      describeBuildChange(
        change({
          status: "reordered",
          best_before: 300,
          best_after: 300,
          delta: 0,
          entered: [relic("Gilded Greatrune")],
          left: [relic("Old Faithful", 2, true)],
        }),
      ),
    ).toBeNull()
  })

  it("reordered stays suppressed even when a relic left the save", () => {
    // The relics moved and one is gone, but the verdict is still "same
    // strength" — nothing here changes what the user would do.
    expect(
      describeBuildChange(
        change({
          status: "reordered",
          best_before: 300,
          best_after: 300,
          delta: 0,
          entered: [relic("Gilded Greatrune")],
          left: [relic("Old Faithful", 2, false)],
        }),
      ),
    ).toBeNull()
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

  describe("cross-version comparison (comparable: false)", () => {
    it("withdraws the % verdict, keeping the relics that moved", () => {
      // The 2026-08-12 regression, exactly: an upload ADDED relics and also
      // crossed the v4 -> v5 optimizer boundary, so the relics hash moved too.
      // The relic movement is real news; the -12% is measured against a
      // different rule set and must not be shown.
      const d = describeBuildChange(
        change({
          status: "degraded",
          best_before: 400,
          best_after: 352,
          delta: -48,
          comparable: false,
          causes: ["relics", "game_data"],
          left: [relic("Benched Horn", 1)],
          entered: [relic("Fresh Blade", 2)],
        }),
      )
      expect(d?.headline).toBe("best setup changed")
      expect(d?.headline).not.toMatch(/weaker|stronger|%/)
      expect(d?.tone).toBe("neutral")
      expect(groupNames(d, "No longer used")).toEqual(["Benched Horn"])
      expect(groupNames(d, "Now uses")).toEqual(["Fresh Blade"])
      expect(d?.note).toMatch(/scoring rules changed/)
    })

    it("suppresses the raw-points tooltip too", () => {
      // The hover quoted "400 -> 352 pts", which is the same void claim.
      const d = describeBuildChange(
        change({
          status: "degraded",
          best_before: 400,
          best_after: 352,
          comparable: false,
        }),
      )
      expect(d?.rawScore).toBeUndefined()
      expect(rawScoreTooltip(d?.rawScore)).toBeUndefined()
    })

    it("withdraws an improvement as readily as a regression", () => {
      const d = describeBuildChange(
        change({
          status: "improved",
          best_before: 100,
          best_after: 150,
          comparable: false,
        }),
      )
      expect(d?.headline).toBe("best setup changed")
      expect(d?.tone).not.toBe("up")
    })

    it('suppresses "same strength" rather than withdrawing it', () => {
      // A rules change makes the same-strength verdict meaningless, and with
      // the verdict gone there is nothing left in a reordered change to say.
      expect(
        describeBuildChange(
          change({
            status: "reordered",
            best_before: 100,
            best_after: 100,
            comparable: false,
          }),
        ),
      ).toBeNull()
    })

    it("leaves statuses that make no score claim alone", () => {
      expect(
        describeBuildChange(
          change({
            status: "broken_pin",
            comparable: false,
            pinned_removed: [relic("Pinned", 1, false)],
          }),
        )?.headline,
      ).toBe("a pinned relic left your save")
      expect(
        describeBuildChange(
          change({
            status: "potentially_affected",
            comparable: false,
            relevant_added: 2,
          }),
        )?.headline,
      ).toBe("2 new relics may help")
      // Same layout, same relics: nothing moved, so there is nothing to say.
      expect(
        describeBuildChange(change({ status: "unchanged", comparable: false })),
      ).toBeNull()
    })

    it("is still news, so the change surfaces without its percentage", () => {
      const c = change({
        status: "degraded",
        comparable: false,
        causes: ["relics", "game_data"],
      })
      expect(isChangeNews(c)).toBe(true)
    })

    it("defaults to comparable when the field is absent (older snapshots)", () => {
      const d = describeBuildChange(
        change({ status: "degraded", best_before: 400, best_after: 352 }),
      )
      expect(d?.headline).toBe("12% weaker")
      expect(d?.rawScore).toEqual({ before: 400, after: 352, delta: -48 })
    })
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

/**
 * Which changes are the user's news and which are an echo of their own edit.
 * A committed Relic Rites purchase counts as news — the Murk is spent, the
 * relic is owned — where it used to be suppressed as a hypothetical.
 */
describe("change causes", () => {
  it("reads the causes list", () => {
    expect(changeCauses(change({ causes: ["relics", "staged"] }))).toEqual([
      "relics",
      "staged",
    ])
  })

  it("falls back to the legacy single cause on older snapshots", () => {
    expect(changeCauses(change({ cause: "relics" }))).toEqual(["relics"])
    // "mixed" carried no detail; the surfaces already read it as save-driven.
    expect(changeCauses(change({ cause: "mixed" }))).toEqual(["relics"])
    expect(changeCauses(change({}))).toEqual([])
  })

  it("treats a newer save and Rites purchases as news", () => {
    expect(isChangeNews(change({ causes: ["relics"] }))).toBe(true)
    expect(isChangeNews(change({ causes: ["staged"] }))).toBe(true)
    expect(isChangeNews(change({ causes: ["relics", "staged"] }))).toBe(true)
  })

  it("stays silent for the user's own edits and for no movement", () => {
    expect(isChangeNews(change({ causes: ["build_edit"] }))).toBe(false)
    expect(isChangeNews(change({ causes: ["game_data"] }))).toBe(false)
    expect(isChangeNews(change({ causes: [] }))).toBe(false)
    expect(isChangeNews(null)).toBe(false)
  })

  it("a build edit alongside real news is still news", () => {
    expect(isChangeNews(change({ causes: ["relics", "build_edit"] }))).toBe(
      true,
    )
  })
})

describe("staged (Relic Rites) relics in a change", () => {
  const bought = (name: string): RelicRef => ({
    ...relic(name, 7),
    staged: true,
  })

  it("says the relics are still owed to the save file", () => {
    const d = describeBuildChange(
      change({
        status: "improved",
        best_before: 100,
        best_after: 150,
        entered: [bought("Deep Burning Scene")],
        causes: ["staged"],
      }),
    ) as ChangeDescription
    expect(d.headline).toBe("50% stronger")
    expect(d.note).toContain("Relic Rites")
    expect(d.note).toContain("export")
  })

  it("counts them, and keeps any existing note", () => {
    const d = describeBuildChange(
      change({
        status: "degraded",
        best_before: 200,
        best_after: 150,
        entered: [bought("A"), bought("B")],
        left: [relic("Old", 2, true)],
        causes: ["relics", "staged"],
      }),
    ) as ChangeDescription
    expect(d.note).toContain("still in your save")
    expect(d.note).toContain("2 relics are")
  })

  it("says nothing when every relic came from the save", () => {
    const d = describeBuildChange(
      change({
        status: "improved",
        best_before: 100,
        best_after: 150,
        entered: [relic("From the game")],
        causes: ["relics"],
      }),
    ) as ChangeDescription
    expect(d.note).toBeUndefined()
  })
})
