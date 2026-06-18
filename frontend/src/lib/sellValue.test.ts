import { describe, expect, it } from "vitest"

import { effectCountOf, sellValue } from "./sellValue"

const EMPTY = 4294967295

describe("sellValue", () => {
  it("matches in-game prices by property count", () => {
    expect(sellValue(1, false)).toBe(150)
    expect(sellValue(2, false)).toBe(350)
    expect(sellValue(3, false)).toBe(550)
  })

  it("doubles for deep relics", () => {
    expect(sellValue(1, true)).toBe(300)
    expect(sellValue(2, true)).toBe(700)
    expect(sellValue(3, true)).toBe(1100)
  })

  it("clamps property count into [1, 3]", () => {
    expect(sellValue(0, false)).toBe(150)
    expect(sellValue(5, false)).toBe(550)
  })
})

describe("effectCountOf", () => {
  it("counts non-empty effects", () => {
    expect(effectCountOf([100, 200, 300])).toBe(3)
    expect(effectCountOf([100, EMPTY, EMPTY])).toBe(1)
    expect(effectCountOf([100, 0, EMPTY])).toBe(1)
    expect(effectCountOf([EMPTY, EMPTY, EMPTY])).toBe(0)
  })
})
