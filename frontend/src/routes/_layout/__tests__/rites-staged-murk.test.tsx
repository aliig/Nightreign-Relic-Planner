/**
 * Live Murk emulation on the Relic Rites page.
 *
 * User journey under test (the reported bug): spend Murk in rites, stage the
 * keepers to Changes, come back to the page — the wallet must show the
 * spent-down value, the overspend guard must gate on it, and a re-run must
 * carry the full staged diff (sold_handles + staged_mints + staged_murk_delta)
 * so the backend plans against the live state instead of the stale save.
 *
 * Strategy mirrors builds-editor.test.tsx: mock createFileRoute to reach the
 * page component, force the anonymous path (sessionStorage profile + local
 * builds), and use the REAL pendingChanges store — tests stage edits exactly
 * like the app does.
 */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import React from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  addMints,
  clearAll,
  setFavorite,
  toggleSell,
} from "@/lib/pendingChanges"
import { formatMurks } from "@/lib/sellValue"

// ---------------------------------------------------------------------------
// Mocks (must be declared before imports under test)
// ---------------------------------------------------------------------------

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@tanstack/react-router")>()
  return {
    ...mod,
    createFileRoute: () => (config: Record<string, unknown>) => config,
    Link: ({
      to,
      children,
      ...rest
    }: {
      to: unknown
      children: React.ReactNode
    }) => (
      <a href={String(to)} {...rest}>
        {children}
      </a>
    ),
  }
})

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@tanstack/react-query")>()
  return {
    ...mod,
    useSuspenseQuery: (opts: { queryKey: unknown[] }) => {
      const key = opts.queryKey as [string, string]
      if (key[0] === "game" && key[1] === "effects")
        return { data: [{ id: 111, name: "Poise +1", is_debuff: false }] }
      return { data: [] }
    },
  }
})

vi.mock("@/client", () => ({
  GameService: { getEffects: vi.fn() },
  BuildsService: { listBuilds: vi.fn() },
  SavesService: { listProfiles: vi.fn() },
}))

vi.mock("@/hooks/useAuth", () => ({
  isLoggedIn: () => false,
}))

vi.mock("@/hooks/useLocalBuilds", () => ({
  useLocalBuilds: () => ({
    builds: [{ id: "b1", name: "Build One", character: "Wylder" }],
  }),
  toInlineBuild: (b: { name: string; character: string }) => ({
    name: b.name,
    character: b.character,
  }),
}))

vi.mock("@/hooks/useCustomToast", () => ({
  default: () => ({ showSuccessToast: vi.fn(), showErrorToast: vi.fn() }),
}))

vi.mock("@/lib/saveFile", () => ({
  getSaveFile: () => new File([new Uint8Array([1, 2, 3])], "save.sl2"),
}))

vi.mock("@/lib/saveBackup", () => ({
  getOriginalBackupFile: async () => null,
}))

// ---------------------------------------------------------------------------
// Import after mocks
// ---------------------------------------------------------------------------

import { Route } from "../rites"

const RitesPage = (Route as unknown as { component: React.FC }).component

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const SLOT = 0
const SAVE_MURKS = 100_000

const MINT_SPEC = {
  real_id: 200,
  item_id: 200 + 0x80000000,
  effects: [111, 4294967295, 4294967295],
  curses: [4294967295, 4294967295, 4294967295],
  name: "Test Relic",
  color: "Red",
  tier: "Delicate",
  isDeep: false,
  oddsSource: "exact",
}

function seedProfile(murks: number) {
  sessionStorage.setItem(
    "parsedProfiles",
    JSON.stringify([{ slot_index: SLOT, name: "Hero", murks }]),
  )
}

function renderRites() {
  return render(
    <React.Suspense fallback={null}>
      <RitesPage />
    </React.Suspense>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  sessionStorage.clear()
  clearAll()
  seedProfile(SAVE_MURKS)
})

afterEach(cleanup)

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("Rites page — live Murk emulation", () => {
  it("shows the spent-down wallet after staging a rites batch (the bug)", () => {
    addMints(SLOT, [MINT_SPEC], -87_600)
    renderRites()
    // Header shows the effective wallet, with the save/staged breakdown.
    expect(screen.getByText(formatMurks(12_400))).toBeInTheDocument()
    expect(
      screen.getByText(
        (t) =>
          t.includes(`save ${formatMurks(SAVE_MURKS)}`) &&
          t.includes(formatMurks(87_600)),
      ),
    ).toBeInTheDocument()
  })

  it("shows the raw save wallet when nothing is staged", () => {
    renderRites()
    expect(screen.getByText(formatMurks(SAVE_MURKS))).toBeInTheDocument()
    expect(screen.queryByText(/staged/)).not.toBeInTheDocument()
  })

  it("gates the overspend guard on the LIVE wallet, not the save value", () => {
    // Default fixed order = 50 scenics = 30,000 Murk: affordable against the
    // save's 100,000, NOT against the spent-down 12,400. Without live
    // emulation this button would happily double-spend.
    addMints(SLOT, [MINT_SPEC], -87_600)
    renderRites()
    expect(screen.getByText(/more than you have/)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /find keepers/i })).toBeDisabled()
  })

  it("sends the full staged diff with a re-run so the plan uses live state", async () => {
    addMints(SLOT, [MINT_SPEC], -5_000)
    toggleSell(SLOT, 777, { name: "Old Relic", murk: 350 })
    setFavorite(SLOT, 888, false)
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: false,
      body: null,
      json: async () => ({ detail: "boom" }),
    } as unknown as Response)

    renderRites()
    fireEvent.click(screen.getByRole("button", { name: /find keepers/i }))
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1))

    const body = fetchSpy.mock.calls[0][1]?.body as FormData
    // Staged sells ride in EVERY mode now (default here is "fixed").
    expect(body.get("stop_mode")).toBe("fixed")
    expect(body.get("sold_handles")).toBe(JSON.stringify([777]))
    // Mints travel by synthetic handle + content; the delta spends the wallet.
    const mints = JSON.parse(String(body.get("staged_mints")))
    expect(mints).toHaveLength(1)
    expect(mints[0].real_id).toBe(200)
    expect(mints[0].handle).toBeLessThan(0)
    expect(body.get("staged_murk_delta")).toBe("-5000")
    // Staged bookmark toggles drive the server's protected-sell gate.
    expect(body.get("staged_favorites")).toBe(JSON.stringify({ 888: false }))
    fetchSpy.mockRestore()
  })

  it("sends no staged fields when the slot is clean", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: false,
      body: null,
      json: async () => ({ detail: "boom" }),
    } as unknown as Response)

    renderRites()
    fireEvent.click(screen.getByRole("button", { name: /find keepers/i }))
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1))

    const body = fetchSpy.mock.calls[0][1]?.body as FormData
    expect(body.get("sold_handles")).toBeNull()
    expect(body.get("staged_mints")).toBeNull()
    expect(body.get("staged_murk_delta")).toBeNull()
    expect(body.get("staged_favorites")).toBeNull()
    fetchSpy.mockRestore()
  })
})
