import { describe, expect, it } from "vitest"

import { relicStatus } from "./relicStatus"

describe("relicStatus", () => {
  it("equipped + used → active (keep)", () => {
    expect(relicStatus(true, true)).toBe("active")
  })

  it("equipped + unused → stale (the in-game cleanup target)", () => {
    expect(relicStatus(true, false)).toBe("stale")
  })

  it("not equipped + used → bench (a build wants it)", () => {
    expect(relicStatus(false, true)).toBe("bench")
  })

  it("not equipped + unused → unused (safe to sell)", () => {
    expect(relicStatus(false, false)).toBe("unused")
  })
})
