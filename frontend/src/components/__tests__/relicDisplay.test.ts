import { describe, expect, it } from "vitest"

import { buildEffectMap, effectBonus } from "../RelicDisplay"

describe("buildEffectMap + effectBonus", () => {
  it("captures bonus_display for an effect id and its aliases", () => {
    buildEffectMap([
      {
        id: 100,
        name: "Magic Power +4",
        bonus_display: "+15%",
        alias_ids: [101],
      },
      { id: 200, name: "No-bonus effect", alias_ids: [] },
    ])
    expect(effectBonus(100)).toBe("+15%")
    expect(effectBonus(101)).toBe("+15%") // alias resolves to the same bonus
    expect(effectBonus(200)).toBeUndefined() // effects without a value have none
    expect(effectBonus(999)).toBeUndefined()
  })
})
