/**
 * Render tests for the change-relic chips — the surface that replaced bare
 * relic names ("lost The Will of Balance") in every save-diff view.
 *
 * Guards the wiring the pure buildChange tests can't see: that each group's
 * label and its relics reach the DOM, and that capping the visible chips never
 * silently drops one.
 */
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import type { RelicRef } from "@/client"
import { ChangeRelicGroups } from "@/components/ChangeRelics"
import type { ChangeRelicGroup } from "@/lib/buildChange"

afterEach(cleanup)

const EFFECT_MAP = new Map<number, string>([
  [10, "Attack Power Up"],
  [99, "HP Down"],
])

function relic(name: string, real_id: number, extra: Partial<RelicRef> = {}) {
  return {
    real_id,
    name,
    color: "Blue",
    tier: "Grand",
    is_deep: false,
    effects: [10],
    curses: [99],
    still_owned: null,
    ...extra,
  } satisfies RelicRef
}

function group(
  kind: ChangeRelicGroup["kind"],
  label: string,
  relics: RelicRef[],
): ChangeRelicGroup {
  return { kind, label, relics }
}

describe("ChangeRelicGroups", () => {
  it("renders nothing when no relics moved", () => {
    const { container } = render(
      <ChangeRelicGroups groups={[]} effectMap={EFFECT_MAP} />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it("labels each group and names its relics", () => {
    render(
      <ChangeRelicGroups
        groups={[
          group("gone", "No longer in your save", [
            relic("Sold Relic", 1, { still_owned: false }),
          ]),
          group("benched", "No longer used", [
            relic("The Will of Balance", 2, { still_owned: true }),
          ]),
        ]}
        effectMap={EFFECT_MAP}
      />,
    )
    expect(screen.getByText("No longer in your save")).toBeTruthy()
    expect(screen.getByText("Sold Relic")).toBeTruthy()
    expect(screen.getByText("No longer used")).toBeTruthy()
    expect(screen.getByText("The Will of Balance")).toBeTruthy()
  })

  it("caps visible chips and keeps the rest behind an overflow count", () => {
    render(
      <ChangeRelicGroups
        groups={[
          group("entered", "Now uses", [
            relic("A", 1),
            relic("B", 2),
            relic("C", 3),
            relic("D", 4),
          ]),
        ]}
        effectMap={EFFECT_MAP}
        max={2}
      />,
    )
    expect(screen.getByText("A")).toBeTruthy()
    expect(screen.getByText("B")).toBeTruthy()
    expect(screen.queryByText("C")).toBeNull()
    expect(screen.getByText("+2 more")).toBeTruthy()
  })

  it("falls back to the relic id when a name is missing", () => {
    render(
      <ChangeRelicGroups
        groups={[group("entered", "Now uses", [relic("", 4242)])]}
        effectMap={EFFECT_MAP}
      />,
    )
    expect(screen.getByText("Relic 4242")).toBeTruthy()
  })
})
