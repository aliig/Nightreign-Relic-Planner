import { describe, expect, it } from "vitest"

import { isUniqueRelic } from "./types"

describe("isUniqueRelic", () => {
  it("flags the unique_1 range (1000–2100) inclusive of bounds", () => {
    expect(isUniqueRelic(1000)).toBe(true)
    expect(isUniqueRelic(1500)).toBe(true)
    expect(isUniqueRelic(2100)).toBe(true)
  })

  it("flags the unique_2 range (10000–19999) inclusive of bounds", () => {
    expect(isUniqueRelic(10000)).toBe(true) // Old Pocketwatch
    expect(isUniqueRelic(10001)).toBe(true) // Besmirched Frame
    expect(isUniqueRelic(19999)).toBe(true)
  })

  it("does not flag ids outside the unique ranges", () => {
    expect(isUniqueRelic(999)).toBe(false)
    expect(isUniqueRelic(2101)).toBe(false)
    expect(isUniqueRelic(9999)).toBe(false)
    expect(isUniqueRelic(20000)).toBe(false)
  })
})
