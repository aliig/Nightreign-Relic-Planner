/**
 * Live Murk emulation + auto-committed batches on the Relic Rites page.
 *
 * The model under test: rolling IS buying. A completed purchase run
 * immediately commits its batch (keepers minted, duds sold, net delta spent) —
 * there is no separate "stage" step, so even an all-dud batch costs its
 * buy/sell spread. Batches STACK: running again is another trip to the shop,
 * so it rides the earlier batches along as staged state, advances the roll
 * epoch (new rolls, not a re-view of the last stream), and appends its own
 * receipt. Purchases live in the staged diff, so they survive navigation.
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
  appendRitesBatch,
  clearAll,
  readSlot,
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
const EMPTY = 4294967295

const MINT_SPEC = {
  real_id: 200,
  item_id: 200 + 0x80000000,
  effects: [111, EMPTY, EMPTY],
  curses: [EMPTY, EMPTY, EMPTY],
  name: "Test Relic",
  color: "Red",
  tier: "Delicate",
  isDeep: false,
  oddsSource: "exact",
}

const RECEIPT = {
  rolled: 10,
  kept: 1,
  cost: 6_000,
  refunded: 1_000,
  label: "Test batch",
}

/** A PlanResponse the page can render, with the given keepers + delta. */
function planPayload(
  keepers: Array<Record<string, unknown>>,
  murkDelta: number,
) {
  return {
    keepers,
    generated: 10,
    kept: keepers.length,
    duds: 10 - keepers.length,
    murk_before: SAVE_MURKS,
    murk_after: SAVE_MURKS + murkDelta,
    murk_cost: 6_000,
    murk_refunded: 6_000 + murkDelta,
    murk_delta: murkDelta,
    limited_by: null,
    add_capacity: 50,
    storage_left: 100,
    pending_sold: 0,
    pending_sold_refund: 0,
    rule_sold: 0,
  }
}

const KEEPER = {
  real_id: 200,
  item_id: 200 + 0x80000000,
  color: "Red",
  tier: "1",
  is_deep: false,
  name: "Test Relic",
  effects: [111, EMPTY, EMPTY],
  curses: [EMPTY, EMPTY, EMPTY],
  builds: [],
}

/** Minimal SSE Response: emits the given events, then ends the stream. */
function sseResponse(events: unknown[]): Response {
  const bytes = new TextEncoder().encode(
    events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join(""),
  )
  let sent = false
  return {
    ok: true,
    body: {
      getReader: () => ({
        read: async () => {
          if (sent) return { done: true, value: undefined }
          sent = true
          return { done: false, value: bytes }
        },
      }),
    },
  } as unknown as Response
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
  it("shows the spent-down wallet while a rites batch is committed", () => {
    appendRitesBatch(SLOT, [MINT_SPEC], -87_600, RECEIPT)
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

  it("gates the overspend guard on the LIVE wallet (batches are paid for)", () => {
    // Default fixed order = 50 scenics = 30,000 Murk. The committed batch has
    // already spent the wallet down to 12,400, and the next batch buys from
    // what is left — so the order is now unaffordable.
    appendRitesBatch(SLOT, [MINT_SPEC], -87_600, RECEIPT)
    renderRites()
    expect(screen.getByText(/more than you have/)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /buy relics/i })).toBeDisabled()
  })

  it("gates the overspend guard on a small save wallet", () => {
    // 50 scenics = 30,000 Murk against a save holding only 20,000.
    sessionStorage.clear()
    seedProfile(20_000)
    renderRites()
    expect(screen.getByText(/more than you have/)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /buy relics/i })).toBeDisabled()
  })

  it("sends sells, favorites AND the committed batches with the next run", async () => {
    appendRitesBatch(SLOT, [MINT_SPEC], -5_000, RECEIPT)
    toggleSell(SLOT, 777, { name: "Old Relic", murk: 350 })
    setFavorite(SLOT, 888, false)
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: false,
      body: null,
      json: async () => ({ detail: "boom" }),
    } as unknown as Response)

    renderRites()
    fireEvent.click(screen.getByRole("button", { name: /buy relics/i }))
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1))

    const body = fetchSpy.mock.calls[0][1]?.body as FormData
    // Staged sells ride in EVERY mode (default here is "fixed")...
    expect(body.get("stop_mode")).toBe("fixed")
    expect(body.get("sold_handles")).toBe(JSON.stringify([777]))
    // ...and staged bookmark toggles drive the server's protected-sell gate.
    expect(body.get("staged_favorites")).toBe(JSON.stringify({ 888: false }))
    // The earlier batch is part of the world now: its relics are owned and
    // its Murk is spent, so both ride along...
    expect(JSON.parse(String(body.get("staged_mints")))).toHaveLength(1)
    expect(body.get("staged_murk_delta")).toBe("-5000")
    // ...and this is the SECOND batch, so it rolls a fresh stream.
    expect(body.get("roll_epoch")).toBe("1")
    fetchSpy.mockRestore()
  })

  it("sends no staged fields when the slot is clean", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: false,
      body: null,
      json: async () => ({ detail: "boom" }),
    } as unknown as Response)

    renderRites()
    fireEvent.click(screen.getByRole("button", { name: /buy relics/i }))
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1))

    const body = fetchSpy.mock.calls[0][1]?.body as FormData
    expect(body.get("sold_handles")).toBeNull()
    expect(body.get("staged_mints")).toBeNull()
    expect(body.get("staged_murk_delta")).toBeNull()
    expect(body.get("staged_favorites")).toBeNull()
    expect(body.get("roll_epoch")).toBe("0")
    fetchSpy.mockRestore()
  })

  it("sends the target when spending down to a set amount", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: false,
      body: null,
      json: async () => ({ detail: "boom" }),
    } as unknown as Response)

    renderRites()
    // The stop-mode picker is a Radix Select; drive the page through it.
    fireEvent.click(screen.getByRole("combobox", { name: "" }))
    fireEvent.click(screen.getByText("Murk is down to a set amount"))
    fireEvent.change(screen.getByLabelText("Murk to stop at"), {
      target: { value: "40000" },
    })
    fireEvent.click(screen.getByRole("button", { name: /buy relics/i }))
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1))

    const body = fetchSpy.mock.calls[0][1]?.body as FormData
    expect(body.get("stop_mode")).toBe("murk_target")
    expect(body.get("target_murk")).toBe("40000")
    fetchSpy.mockRestore()
  })
})

describe("Rites page — auto-committed batches (roll = purchase)", () => {
  it("commits the batch the moment a plan completes; trashing sells back", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        sseResponse([{ type: "result", data: planPayload([KEEPER], -5_000) }]),
      )
    renderRites()
    fireEvent.click(screen.getByRole("button", { name: /buy relics/i }))

    // Completion alone staged the keeper AND spent the delta — no extra click.
    await waitFor(() => expect(readSlot(SLOT).mints).toHaveLength(1))
    expect(readSlot(SLOT).mints[0].real_id).toBe(200)
    expect(readSlot(SLOT).murkDelta).toBe(-5_000)
    expect(readSlot(SLOT).batches).toHaveLength(1)

    // Trashing the relic sells it back: mint gone, sell value credited
    // (1-effect normal relic -> 150), the batch itself still committed.
    fireEvent.click(
      await screen.findByRole("button", { name: /sell test relic back/i }),
    )
    await waitFor(() => expect(readSlot(SLOT).mints).toHaveLength(0))
    expect(readSlot(SLOT).murkDelta).toBe(-5_000 + 150)
    expect(readSlot(SLOT).batches).toHaveLength(1)
    fetchSpy.mockRestore()
  })

  it("commits the loss of an all-dud batch (zero keepers — the reported bug)", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        sseResponse([{ type: "result", data: planPayload([], -4_150) }]),
      )
    renderRites()
    fireEvent.click(screen.getByRole("button", { name: /buy relics/i }))

    // Nothing to keep, but the buy/sell spread is spent all the same.
    await waitFor(() => expect(readSlot(SLOT).murkDelta).toBe(-4_150))
    expect(readSlot(SLOT).mints).toHaveLength(0)
    expect(await screen.findByText(/Nothing kept/)).toBeInTheDocument()
    fetchSpy.mockRestore()
  })

  it("a second run stacks a new batch instead of replacing the first", async () => {
    appendRitesBatch(SLOT, [], -4_150, { ...RECEIPT, kept: 0 })
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        sseResponse([{ type: "result", data: planPayload([KEEPER], -5_000) }]),
      )
    renderRites()
    fireEvent.click(screen.getByRole("button", { name: /buy relics/i }))

    await waitFor(() => expect(readSlot(SLOT).mints).toHaveLength(1))
    // Both trips to the shop are paid for.
    expect(readSlot(SLOT).murkDelta).toBe(-9_150)
    expect(readSlot(SLOT).batches).toHaveLength(2)
    fetchSpy.mockRestore()
  })

  it("shows committed purchases without re-running (they survive navigation)", () => {
    // A batch staged in an earlier visit renders straight from the diff.
    appendRitesBatch(SLOT, [MINT_SPEC], -5_000, RECEIPT)
    renderRites()
    expect(screen.getByText("Test Relic")).toBeInTheDocument()
    expect(screen.getByText("Batch 1")).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: /sell test relic back/i }),
    ).toBeInTheDocument()
  })

  it("cancelling a batch un-buys it and frees its roll epoch", async () => {
    appendRitesBatch(SLOT, [MINT_SPEC], -5_000, RECEIPT)
    renderRites()
    fireEvent.click(screen.getByRole("button", { name: /cancel batch/i }))
    await waitFor(() => expect(readSlot(SLOT).batches).toHaveLength(0))
    expect(readSlot(SLOT).mints).toHaveLength(0)
    expect(readSlot(SLOT).murkDelta).toBe(0)
  })
})
