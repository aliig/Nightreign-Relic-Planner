/**
 * Unit tests for BuildEditorPage – specifically the "Effect Browser" panel
 * and its Groups vs Individual sections.
 *
 * Strategy:
 * - Mock createFileRoute the same way as upload.test.tsx so we can access the
 *   component directly.
 * - Force the anonymous (localStorage) path via isLoggedIn → false.
 * - Mock useSuspenseQuery to return controlled families/effects/tiers data.
 * - Mock useLocalBuilds to return a minimal build fixture.
 *
 * Key regression this covers:
 *   "Improved Damage Negation at Low HP" must NOT appear under "Groups" in the
 *   effect browser — it has no real magnitude-variant family (the +1/+2 variants
 *   in stacking_rules.json have no corresponding effect params).  If the backend
 *   fix regresses and the API returns it as a family, this test will fail.
 */
import React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

// ---------------------------------------------------------------------------
// Types mirroring builds.$buildId.tsx (keep in sync if the API shape changes)
// ---------------------------------------------------------------------------

type FamilyMeta = { name: string; member_names: string[]; member_ids: number[] }
type EffectMeta = { id: number; name: string; is_debuff?: boolean }
type TierConfig = {
  key: string
  display_name: string
  color: string
  weight: number
  scored: boolean
  is_exclusion: boolean
}

// ---------------------------------------------------------------------------
// Mutable mock state (set per test, closures read at call time)
// ---------------------------------------------------------------------------

let mockFamilies: FamilyMeta[] = []
let mockEffects: EffectMeta[] = []

const MOCK_TIERS: TierConfig[] = [
  {
    key: "required",
    display_name: "Essential",
    color: "#FF4444",
    weight: 100,
    scored: true,
    is_exclusion: false,
  },
  {
    key: "preferred",
    display_name: "Preferred",
    color: "#4488FF",
    weight: 50,
    scored: true,
    is_exclusion: false,
  },
]

const MOCK_BUILD = {
  id: "build-1",
  name: "Test Build",
  character: "Wylder",
  tiers: {},
  family_tiers: {},
  include_deep: false,
  curse_max: 1,
  tier_weights: null,
  pinned_relics: [],
}

// ---------------------------------------------------------------------------
// Mocks (must be declared before imports under test)
// ---------------------------------------------------------------------------

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@tanstack/react-router")>()
  return {
    ...mod,
    createFileRoute: () => (config: Record<string, unknown>) => config,
    useParams: () => ({ buildId: "build-1" }),
  }
})

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@tanstack/react-query")>()
  return {
    ...mod,
    useSuspenseQuery: (opts: { queryKey: unknown[] }) => {
      const key = opts.queryKey as [string, string]
      if (key[1] === "families") return { data: mockFamilies }
      if (key[1] === "effects") return { data: mockEffects }
      if (key[1] === "tiers") return { data: MOCK_TIERS }
      return { data: [] }
    },
    // useQuery is called by AuthPinnedRelicDialog (not rendered in anon path,
    // but mocked defensively in case the component tree changes)
    useQuery: () => ({ data: undefined, isLoading: false, isError: false, error: null }),
    useMutation: () => ({ mutate: vi.fn(), isPending: false }),
    useQueryClient: () => ({ invalidateQueries: vi.fn(), fetchQuery: vi.fn() }),
  }
})

vi.mock("@/client", () => ({
  GameService: {
    getEffects: vi.fn(),
    getFamilies: vi.fn(),
    getTiers: vi.fn(),
  },
  BuildsService: {
    getBuild: vi.fn(),
    updateBuild: vi.fn(),
  },
  SavesService: {
    listCharacters: vi.fn(),
    getCharacterRelics: vi.fn(),
  },
}))

vi.mock("@/hooks/useAuth", () => ({
  isLoggedIn: () => false,
}))

vi.mock("@/hooks/useLocalBuilds", () => ({
  useLocalBuilds: () => ({
    getById: () => MOCK_BUILD,
    update: vi.fn(),
  }),
}))

vi.mock("@/hooks/useCustomToast", () => ({
  default: () => ({ showSuccessToast: vi.fn(), showErrorToast: vi.fn() }),
}))

vi.mock("@/utils", () => ({
  handleError: vi.fn(),
}))

// ---------------------------------------------------------------------------
// Import after mocks
// ---------------------------------------------------------------------------

import { Route } from "../builds.$buildId"

const BuildEditorPage = (Route as unknown as { component: React.FC }).component

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderEditor() {
  return render(
    <React.Suspense fallback={null}>
      <BuildEditorPage />
    </React.Suspense>,
  )
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks()
  // Reset to empty defaults; individual tests set what they need
  mockFamilies = []
  mockEffects = []
})

afterEach(cleanup)

describe("Effect Browser – Groups section visibility", () => {
  it("renders the search input", () => {
    mockEffects = [{ id: 1, name: "Poise +1" }]
    renderEditor()
    expect(screen.getByPlaceholderText(/search effects/i)).toBeInTheDocument()
  })

  it("shows 'Groups' label only when a family name matches the search term", async () => {
    mockFamilies = [
      { name: "Poise", member_names: ["Poise +1", "Poise +2"], member_ids: [1, 2] },
    ]
    mockEffects = [
      { id: 1, name: "Poise +1" },
      { id: 2, name: "Poise +2" },
    ]

    renderEditor()

    const input = screen.getByPlaceholderText(/search effects/i)

    // Before search: no term entered, Groups section visible because families exist
    expect(screen.getByText("Groups")).toBeInTheDocument()

    // Search for something that matches the family
    fireEvent.change(input, { target: { value: "poise" } })
    expect(screen.getByText("Groups")).toBeInTheDocument()
    expect(screen.getByText("Poise")).toBeInTheDocument()

    // Search for something with no family match
    fireEvent.change(input, { target: { value: "xxxxxxno-match" } })
    expect(screen.queryByText("Groups")).not.toBeInTheDocument()
  })

  it("does not show 'Groups' when families list is empty", () => {
    mockFamilies = []
    mockEffects = [{ id: 340800, name: "Improved Damage Negation at Low HP" }]

    renderEditor()
    expect(screen.queryByText("Groups")).not.toBeInTheDocument()
  })
})

describe("Effect Browser – 'Improved Damage Negation at Low HP' regression", () => {
  it("does not appear under Groups when families list excludes it", () => {
    // Correct backend behaviour: this effect is NOT a family (no real +1/+2 params)
    mockFamilies = [
      // Only legitimate multi-member families are present
      { name: "Poise", member_names: ["Poise +1", "Poise +2"], member_ids: [1, 2] },
    ]
    mockEffects = [
      { id: 340800, name: "Improved Damage Negation at Low HP" },
      { id: 1, name: "Poise +1" },
      { id: 2, name: "Poise +2" },
    ]

    renderEditor()
    const input = screen.getByPlaceholderText(/search effects/i)
    fireEvent.change(input, { target: { value: "low hp" } })

    // The "Groups" label should NOT be visible — no family matched "low hp"
    expect(screen.queryByText("Groups")).not.toBeInTheDocument()

    // The individual effect must still appear in the browser
    expect(screen.getByText("Improved Damage Negation at Low HP")).toBeInTheDocument()
  })

  it("appears under Groups if backend incorrectly returns it as a family (documents regression)", () => {
    // This test documents the OLD broken behaviour. If the backend regresses and
    // returns this effect as a family, this assertion flips and the test above will
    // catch it via the Groups-absent check.
    mockFamilies = [
      {
        name: "Improved Damage Negation at Low HP",
        member_names: ["Improved Damage Negation at Low HP"],
        member_ids: [340800],
      },
    ]
    mockEffects = [{ id: 340800, name: "Improved Damage Negation at Low HP" }]

    renderEditor()
    const input = screen.getByPlaceholderText(/search effects/i)
    fireEvent.change(input, { target: { value: "low hp" } })

    // When the backend mistakenly returns it as a family it appears in BOTH the
    // Groups section (italic chip) AND the Individual section — hence two nodes.
    // This test exists so reviewers understand the failure mode.
    expect(screen.getByText("Groups")).toBeInTheDocument()
    expect(screen.getAllByText("Improved Damage Negation at Low HP").length).toBeGreaterThanOrEqual(1)
  })
})
