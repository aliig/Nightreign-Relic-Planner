import "@testing-library/jest-dom"

import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it, vi } from "vitest"

import {
  type CumulativeEffectGroup,
  OptimizeService,
  type VesselResult,
} from "@/client"
import {
  MissingRequirementsSeparator,
  NoCoveringResultsBanner,
  stackedCurses,
  VesselCard,
} from "@/components/OptimizeResults"

type SlotAssignment = VesselResult["assignments"][number]
type OwnedRelic = NonNullable<SlotAssignment["relic"]>

const EMPTY = 4294967295

function makeRelic(ga_handle: number, name: string, color = "Red"): OwnedRelic {
  return {
    ga_handle,
    item_id: ga_handle,
    real_id: ga_handle,
    color,
    effects: [100, EMPTY, EMPTY],
    curses: [EMPTY, EMPTY, EMPTY],
    is_deep: false,
    name,
    tier: "Delicate",
    effect_count: 1,
    curse_count: 0,
    all_effects: [100],
  }
}

function makeSlot(
  slot_index: number,
  relic: OwnedRelic | null,
): SlotAssignment {
  return {
    slot_index,
    slot_color: "Red",
    is_deep: false,
    relic,
    score: relic ? 100 : 0,
    breakdown: [],
  }
}

function makeVessel(relics: (OwnedRelic | null)[]): VesselResult {
  const assignments = relics.map((r, i) => makeSlot(i, r))
  return {
    vessel_id: 42,
    vessel_name: "Test Vessel",
    vessel_character: "Wylder",
    unlock_flag: 0,
    slot_colors: ["Red", "Red", "Red"],
    assignments,
    total_score: assignments.reduce((s, a) => s + a.score, 0),
    meets_requirements: true,
    missing_requirements: [],
    search_truncated: false,
  }
}

const ALPHA = makeRelic(1, "Relic Alpha")
const BETA = makeRelic(2, "Relic Beta")
const GAMMA = makeRelic(3, "Relic Gamma")
const DELTA = makeRelic(9, "Relic Delta")

const INVENTORY_SOURCE = { build_id: "b1", profile_id: "p1" }

function makeGroup(
  family: string,
  bonus_display: string,
  opts: Partial<CumulativeEffectGroup> = {},
): CumulativeEffectGroup {
  return {
    family,
    mode: "multiplicative",
    unit: "%",
    tiers: [{ name: `${family} +1`, tier_label: "+1", count: 1 }],
    cumulative_value: 1.5,
    bonus_percent: 50,
    bonus_display,
    is_top: false,
    ...opts,
  }
}

const CUMULATIVE: CumulativeEffectGroup[] = [
  makeGroup("Magic Attack Power Up", "1.58× (+58%)", { is_top: true }),
  makeGroup("Vigor", "+60 Max HP", {
    mode: "additive_flat",
    unit: "Max HP",
    bonus_percent: null,
  }),
]

function mockStrike(result: VesselResult | null) {
  // The strike flow goes through the generated SDK, so mock at that seam
  // rather than stubbing global fetch.
  return vi
    .spyOn(OptimizeService, "optimizeSlotAlternative")
    .mockResolvedValue(result as never)
}

describe("Requirement indicator", () => {
  it("hides the check/X icon for builds with no Required entries", () => {
    const { container } = render(<VesselCard vessel={makeVessel([ALPHA])} />)
    expect(container.querySelector("svg.text-green-500")).toBeNull()
    expect(container.querySelector("svg.text-destructive")).toBeNull()
  })

  it("shows a green check on covering results when the build has requirements", () => {
    const { container } = render(
      <VesselCard vessel={makeVessel([ALPHA])} hasRequirements />,
    )
    expect(container.querySelector("svg.text-green-500")).not.toBeNull()
  })

  it("shows a red X on non-covering results when the build has requirements", () => {
    const vessel = {
      ...makeVessel([ALPHA]),
      meets_requirements: false,
      missing_requirements: [100],
    }
    const { container } = render(<VesselCard vessel={vessel} hasRequirements />)
    expect(container.querySelector("svg.text-destructive")).not.toBeNull()
    expect(container.querySelector("svg.text-green-500")).toBeNull()
  })
})

describe("Missing-requirements separator and banner", () => {
  it("renders the separator copy", () => {
    render(<MissingRequirementsSeparator />)
    expect(screen.getByText(/missing required effect/i)).toBeInTheDocument()
  })

  it("renders the zero-covering banner copy", () => {
    render(<NoCoveringResultsBanner />)
    expect(screen.getByText(/closest matches/i)).toBeInTheDocument()
  })
})

describe("VesselCard strike", () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("strikes a relic, sends the right pins/exclusions, and swaps the slot", async () => {
    const strikeMock = mockStrike(makeVessel([ALPHA, DELTA, GAMMA]))
    render(
      <VesselCard
        vessel={makeVessel([ALPHA, BETA, GAMMA])}
        defaultExpanded
        inventorySource={INVENTORY_SOURCE}
      />,
    )

    expect(screen.getByText("Relic Beta")).toBeInTheDocument()
    await userEvent.click(screen.getByLabelText(/Reject Relic Beta/i))

    // The struck slot swaps to the backend's replacement.
    expect(await screen.findByText("Relic Delta")).toBeInTheDocument()
    expect(screen.queryByText("Relic Beta")).not.toBeInTheDocument()

    // Locks every other slot in place (slot_index + relic); excludes the struck one.
    expect(strikeMock).toHaveBeenCalledTimes(1)
    expect(strikeMock).toHaveBeenCalledWith({
      requestBody: expect.objectContaining({
        build_id: "b1",
        profile_id: "p1",
        vessel_id: 42,
        struck_slot_index: 1,
        locked_slots: [
          { slot_index: 0, ga_handle: 1 },
          { slot_index: 2, ga_handle: 3 },
        ],
        excluded_ga_handles: [2],
      }),
    })
  })

  it("resets to the original arrangement", async () => {
    mockStrike(makeVessel([ALPHA, DELTA, GAMMA]))
    render(
      <VesselCard
        vessel={makeVessel([ALPHA, BETA, GAMMA])}
        defaultExpanded
        inventorySource={INVENTORY_SOURCE}
      />,
    )

    await userEvent.click(screen.getByLabelText(/Reject Relic Beta/i))
    expect(await screen.findByText("Relic Delta")).toBeInTheDocument()

    await userEvent.click(screen.getByRole("button", { name: /reset/i }))

    expect(await screen.findByText("Relic Beta")).toBeInTheDocument()
    expect(screen.queryByText("Relic Delta")).not.toBeInTheDocument()
  })

  it("keeps the relic and notes it when no alternative is found", async () => {
    // Backend returns the struck slot empty (no replacement relic fits).
    mockStrike(makeVessel([ALPHA, null, GAMMA]))
    render(
      <VesselCard
        vessel={makeVessel([ALPHA, BETA, GAMMA])}
        defaultExpanded
        inventorySource={INVENTORY_SOURCE}
      />,
    )

    await userEvent.click(screen.getByLabelText(/Reject Relic Beta/i))

    expect(
      await screen.findByText(/No other relic fits this slot/i),
    ).toBeInTheDocument()
    // Relic is kept, and its strike button is disabled.
    expect(screen.getByText("Relic Beta")).toBeInTheDocument()
    expect(screen.getByLabelText(/Reject Relic Beta/i)).toBeDisabled()
  })

  it("renders no strike controls without an inventorySource", () => {
    render(
      <VesselCard vessel={makeVessel([ALPHA, BETA, GAMMA])} defaultExpanded />,
    )
    expect(screen.queryByLabelText(/Reject /i)).not.toBeInTheDocument()
  })
})

describe("VesselCard cumulative summary", () => {
  it("shows the top stacked effect at a glance even when collapsed", () => {
    const vessel = makeVessel([ALPHA, BETA, GAMMA])
    vessel.cumulative_effects = CUMULATIVE
    render(<VesselCard vessel={vessel} />) // collapsed (no defaultExpanded)

    expect(screen.getByText("Magic Attack Power Up")).toBeInTheDocument()
    expect(screen.getByText("1.58× (+58%)")).toBeInTheDocument()
    // The secondary group is hidden until "See all" is opened.
    expect(screen.queryByText("+60 Max HP")).not.toBeInTheDocument()
  })

  it("expands to list every cumulative bonus via 'See all'", async () => {
    const vessel = makeVessel([ALPHA, BETA, GAMMA])
    vessel.cumulative_effects = CUMULATIVE
    render(<VesselCard vessel={vessel} />)

    await userEvent.click(
      screen.getByRole("button", { name: /see all \(2\)/i }),
    )
    expect(screen.getByText("+60 Max HP")).toBeInTheDocument()
  })

  it("renders nothing when there are no cumulative effects", () => {
    render(<VesselCard vessel={makeVessel([ALPHA, BETA, GAMMA])} />)
    expect(screen.queryByText(/see all/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Max HP/i)).not.toBeInTheDocument()
  })
})

describe("Stacked curses", () => {
  /** A slot whose relic carries one curse, with the build's weight for it. */
  function cursedSlot(
    slot_index: number,
    relic: OwnedRelic,
    curse: string,
    opts: { weight?: number; override_status?: string } = {},
  ): SlotAssignment {
    return {
      ...makeSlot(slot_index, relic),
      breakdown: [
        {
          effect_id: 6840000,
          name: curse,
          category: opts.weight ? "custom" : null,
          weight: opts.weight ?? 0,
          score: opts.weight ?? 0,
          is_curse: true,
          redundant: opts.override_status != null,
          override_status: opts.override_status ?? null,
        },
      ],
    }
  }

  const RUNES = "Reduced Rune Acquisition"

  function vesselWith(assignments: SlotAssignment[]): VesselResult {
    return { ...makeVessel([]), assignments }
  }

  it("reports a curse carried by two relics", () => {
    const dupes = stackedCurses([
      cursedSlot(0, ALPHA, RUNES),
      cursedSlot(1, BETA, "Continuous HP Loss"),
      cursedSlot(2, GAMMA, RUNES),
    ])
    expect(dupes).toEqual([{ name: RUNES, count: 2, slots: [0, 2] }])
  })

  it("stays quiet for a single copy", () => {
    expect(stackedCurses([cursedSlot(0, ALPHA, RUNES)])).toEqual([])
  })

  it("stays quiet when the build asked for the curse", () => {
    // Some builds want their curses — a positive weight is a deliberate pick.
    expect(
      stackedCurses([
        cursedSlot(0, ALPHA, RUNES, { weight: 50 }),
        cursedSlot(1, BETA, RUNES, { weight: 50 }),
      ]),
    ).toEqual([])
  })

  it("ignores copies the game itself neutralizes", () => {
    expect(
      stackedCurses([
        cursedSlot(0, ALPHA, RUNES),
        cursedSlot(1, BETA, RUNES, {
          override_status: "character_incompatible",
        }),
      ]),
    ).toEqual([])
  })

  it("warns on the card even when collapsed, and marks the slot rows", async () => {
    const vessel = vesselWith([
      cursedSlot(0, ALPHA, RUNES),
      cursedSlot(1, BETA, RUNES),
    ])
    const { unmount } = render(<VesselCard vessel={vessel} />)
    expect(screen.getByText(RUNES)).toBeInTheDocument()
    expect(screen.getByText("×2")).toBeInTheDocument()
    unmount()

    // Expanded, each offending curse row is marked too (chip + 2 slot rows).
    render(<VesselCard vessel={vessel} defaultExpanded />)
    expect(screen.getAllByText("×2")).toHaveLength(3)
  })

  it("renders no warning when no curse repeats", () => {
    render(
      <VesselCard
        vessel={vesselWith([
          cursedSlot(0, ALPHA, RUNES),
          cursedSlot(1, BETA, "Continuous HP Loss"),
        ])}
      />,
    )
    expect(screen.queryByText("×2")).not.toBeInTheDocument()
  })
})
