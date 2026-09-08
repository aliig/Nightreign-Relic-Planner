/**
 * Unit tests for the inventory table's culling affordances.
 *
 * These pin the three things the user reported as untrustworthy:
 *  - the tier filter narrows to exactly the relics of that tier,
 *  - "select all" says how many it actually selected, and the bulk bar admits
 *    how much of the selection the current filter is hiding,
 *  - a bulk trash asks first, summarises its impact, and lands as ONE staged
 *    write rather than one per relic.
 *
 * Harness note: @tanstack/react-virtual needs a ResizeObserver and a non-zero
 * scroll rect, neither of which jsdom provides — both are stubbed below so the
 * real virtualizer runs and actually mounts rows.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type { BuildUsageInfo, RelicUsage } from "@/hooks/useRelicUsage"
import * as pendingChanges from "@/lib/pendingChanges"
import { RelicManager } from "../RelicManager"
import type { ManagedRelic } from "../types"

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@tanstack/react-router")>()
  return {
    ...mod,
    Link: ({ children }: { children?: React.ReactNode }) => (
      <span>{children}</span>
    ),
  }
})

// react-virtual sizes its viewport from offsetWidth/offsetHeight and each row
// from getBoundingClientRect, and jsdom reports 0 for all three — left alone it
// concludes nothing is on screen and mounts no rows at all.
const VIEWPORT_HEIGHT = 800
const ROW_HEIGHT = 84

class StubResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

function stubLayout(): () => void {
  const proto = HTMLElement.prototype
  const original = {
    width: Object.getOwnPropertyDescriptor(proto, "offsetWidth"),
    height: Object.getOwnPropertyDescriptor(proto, "offsetHeight"),
    rect: proto.getBoundingClientRect,
  }
  Object.defineProperty(proto, "offsetWidth", {
    configurable: true,
    get: () => 1000,
  })
  Object.defineProperty(proto, "offsetHeight", {
    configurable: true,
    get: () => VIEWPORT_HEIGHT,
  })
  proto.getBoundingClientRect = () =>
    ({ width: 1000, height: ROW_HEIGHT, top: 0, left: 0 }) as DOMRect
  return () => {
    if (original.width)
      Object.defineProperty(proto, "offsetWidth", original.width)
    if (original.height)
      Object.defineProperty(proto, "offsetHeight", original.height)
    proto.getBoundingClientRect = original.rect
  }
}

function relic(
  over: Partial<ManagedRelic> & { gaHandle: number },
): ManagedRelic {
  return {
    key: `k${over.gaHandle}`,
    realId: 5000,
    name: `Relic ${over.gaHandle}`,
    color: "Red",
    tier: "Grand",
    isDeep: false,
    effects: [],
    curses: [],
    isFavorite: false,
    equipped: false,
    acquisitionId: null,
    ...over,
  }
}

function usageRow(
  gaHandle: number,
  tier: RelicUsage["tier"],
  over: Partial<RelicUsage> = {},
): RelicUsage {
  return {
    ga_handle: gaHandle,
    tier,
    used_by: [],
    uncertain: false,
    content_group: gaHandle,
    ...over,
  }
}

const RELICS = [
  relic({ gaHandle: 1, name: "Keeper" }),
  relic({ gaHandle: 2, name: "Spare" }),
  relic({ gaHandle: 3, name: "Junk One" }),
  relic({ gaHandle: 4, name: "Junk Two" }),
]

const USAGE = new Map<number, RelicUsage>([
  [
    1,
    usageRow(1, "in_use", {
      used_by: [{ build_id: "b1", rank: 1 }],
    }),
  ],
  [2, usageRow(2, "contender")],
  [3, usageRow(3, "dead")],
  [4, usageRow(4, "dead")],
])

const BUILDS = new Map<string, BuildUsageInfo>([
  [
    "b1",
    { build_id: "b1", name: "Dregs Raider", fresh: true, optimized: true },
  ],
])

function renderManager(over: { usageKnown?: boolean } = {}) {
  return render(
    <RelicManager
      relics={RELICS}
      effectsData={[]}
      effectMap={new Map()}
      usage={over.usageKnown === false ? new Map() : USAGE}
      buildsById={BUILDS}
      usageKnown={over.usageKnown ?? true}
      usageUnavailable={false}
      slotIndex={0}
      murks={0}
    />,
  )
}

/** Open the State facet and tick one cull tier.  The option's accessible name
 *  is its label followed by its hint, so match on the label prefix. */
function selectTier(label: string) {
  fireEvent.click(screen.getByRole("button", { name: /State/ }))
  fireEvent.click(screen.getByRole("button", { name: new RegExp(`^${label}`) }))
}

let restoreLayout: () => void

beforeEach(() => {
  localStorage.clear()
  pendingChanges.clearAll()
  vi.stubGlobal("ResizeObserver", StubResizeObserver)
  restoreLayout = stubLayout()
})

afterEach(() => {
  cleanup()
  restoreLayout()
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe("RelicManager tier filter", () => {
  it("renders every relic's tier badge", () => {
    renderManager()
    expect(screen.getByText("In use")).toBeInTheDocument()
    expect(screen.getByText("Contender")).toBeInTheDocument()
    expect(screen.getAllByText("Dead weight")).toHaveLength(2)
  })

  it("ignores a selected tier while the usage answer has not landed", () => {
    // matchesState lets a null tier through so a row cannot vanish mid-flight.
    // As a FILTER that would list the whole inventory under "Dead weight" and
    // invite select-all + trash, so the axis is dropped until usage is known.
    const { rerender } = renderManager()
    selectTier("Dead weight")
    expect(
      screen.getByRole("checkbox", { name: "Select all 2 matching" }),
    ).toBeInTheDocument()

    // Same component, same chosen tier, usage now unknown: every relic is
    // listed again rather than 1,550 rows masquerading as dead weight.
    rerender(
      <RelicManager
        relics={RELICS}
        effectsData={[]}
        effectMap={new Map()}
        usage={new Map()}
        buildsById={BUILDS}
        usageKnown={false}
        usageUnavailable={false}
        slotIndex={0}
        murks={0}
      />,
    )
    expect(
      screen.getByRole("checkbox", { name: "Select all 4 matching" }),
    ).toBeInTheDocument()
  })

  it("disables the tier control until usage is known", () => {
    renderManager({ usageKnown: false })
    fireEvent.click(screen.getByRole("button", { name: /State/ }))
    expect(screen.getByRole("button", { name: /Dead weight/ })).toBeDisabled()
  })

  it("narrows the list to the chosen tier", () => {
    renderManager()
    selectTier("Dead weight")
    expect(screen.getByText("Junk One")).toBeInTheDocument()
    expect(screen.getByText("Junk Two")).toBeInTheDocument()
    expect(screen.queryByText("Keeper")).not.toBeInTheDocument()
    expect(screen.queryByText("Spare")).not.toBeInTheDocument()
  })
})

describe("RelicManager bulk selection", () => {
  it("labels select-all with the real number of matching relics", () => {
    renderManager()
    expect(
      screen.getByRole("checkbox", { name: "Select all 4 matching" }),
    ).toBeInTheDocument()
    selectTier("Dead weight")
    expect(
      screen.getByRole("checkbox", { name: "Select all 2 matching" }),
    ).toBeInTheDocument()
  })

  it("admits how much of the selection the filter is hiding", () => {
    renderManager()
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Select all 4 matching" }),
    )
    expect(screen.getByText(/4 selected/)).toBeInTheDocument()
    // The selection deliberately survives a filter change, so it has to SAY
    // that rather than look like it silently lost rows.
    selectTier("Dead weight")
    expect(
      screen.getByText(/2 shown by current filter · 2 hidden/),
    ).toBeInTheDocument()
  })
})

describe("RelicManager bulk trash", () => {
  it("confirms first, summarises impact, and stages one write", () => {
    const persist = vi.spyOn(Storage.prototype, "setItem")
    renderManager()
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Select all 4 matching" }),
    )
    persist.mockClear()

    fireEvent.click(screen.getByRole("button", { name: /Trash 4 selected/ }))
    // Nothing is staged until the dialog is confirmed.
    expect(persist).not.toHaveBeenCalled()
    expect(
      screen.getByRole("heading", { name: "Trash 4 relics?" }),
    ).toBeInTheDocument()
    // The summary must break the selection down by tier, not just count it.
    const summary = screen.getAllByRole("listitem").map((li) => li.textContent)
    expect(summary).toContain("1 in use")
    expect(summary).toContain("1 contender")
    expect(summary).toContain("2 dead weight")

    fireEvent.click(screen.getByRole("button", { name: "Trash 4 relics" }))
    expect(persist).toHaveBeenCalledTimes(1)
    expect(pendingChanges.readSlot(0).sells.sort()).toEqual([1, 2, 3, 4])
  })

  it("cancelling stages nothing", () => {
    renderManager()
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Select all 4 matching" }),
    )
    fireEvent.click(screen.getByRole("button", { name: /Trash 4 selected/ }))
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
    expect(pendingChanges.readSlot(0).sells).toEqual([])
  })
})
