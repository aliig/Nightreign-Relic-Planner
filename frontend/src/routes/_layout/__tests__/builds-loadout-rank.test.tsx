/**
 * Unit tests for the builds-list "your in-game loadout is result #N" badge.
 *
 * The badge answers, at a glance, whether what the user actually has saved in
 * their game is still the optimizer's pick for that build. These tests pin the
 * wiring the backend can't: which card a rank lands on, the in-sync vs drifted
 * wording, and the silence when a build has no matching loadout.
 *
 * Strategy mirrors builds-editor.test.tsx: mock createFileRoute so the page
 * component is reachable, and answer react-query by query key.
 */

import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type { LoadoutRank } from "@/client"

const BUILDS = [
  {
    id: "build-1",
    name: "Dregs Raider",
    character: "Duchess",
    groups: [{ weight: 50, effects: [1], families: [] }],
    updated_at: "2026-08-01T00:00:00Z",
    is_featured: false,
  },
  {
    id: "build-2",
    name: "Fire Wylder",
    character: "Wylder",
    groups: [{ weight: 50, effects: [2], families: [] }],
    updated_at: "2026-08-01T00:00:00Z",
    is_featured: false,
  },
]

let mockRanks: LoadoutRank[] = []

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@tanstack/react-router")>()
  return {
    ...mod,
    createFileRoute: () => (config: Record<string, unknown>) => config,
    useRouterState: () => false,
    Link: ({ children }: { children?: React.ReactNode }) => (
      <span>{children}</span>
    ),
  }
})

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@tanstack/react-query")>()
  return {
    ...mod,
    useSuspenseQuery: (opts: { queryKey: unknown[] }) => {
      const key = opts.queryKey as string[]
      if (key[0] === "builds" && key[1] === "featured")
        return { data: { data: [] } }
      if (key[0] === "builds") return { data: { data: BUILDS } }
      return { data: { data: [] } }
    },
    useQuery: (opts: { queryKey: unknown[] }) => {
      const key = opts.queryKey as string[]
      if (key[0] === "build-summaries") return { data: [] }
      if (key[0] === "profiles")
        return {
          data: { data: [{ id: "profile-1", slot_index: 0, name: "Hero" }] },
        }
      if (key[0] === "loadouts")
        return {
          data: {
            data: [
              {
                index: 0,
                character: "Duchess",
                name: "Dregs Raider",
                vessel_id: 10,
                ga_handles: [1, 2, 3],
              },
            ],
          },
        }
      if (key[0] === "loadout-ranks") return { data: mockRanks }
      return { data: undefined }
    },
    useMutation: () => ({
      mutate: vi.fn(),
      isPending: false,
      variables: undefined,
    }),
    useQueryClient: () => ({
      invalidateQueries: vi.fn(),
      removeQueries: vi.fn(),
    }),
  }
})

vi.mock("@/hooks/useAuth", () => ({
  default: () => ({ user: { is_superuser: false } }),
  isLoggedIn: () => true,
}))

vi.mock("@/hooks/useCustomToast", () => ({
  default: () => ({ showSuccessToast: vi.fn(), showErrorToast: vi.fn() }),
}))

vi.mock("@/client", () => ({
  BuildsService: {
    listBuilds: vi.fn(),
    listFeaturedBuilds: vi.fn(),
    deleteBuild: vi.fn(),
    updateBuild: vi.fn(),
    cloneBuild: vi.fn(),
    toggleFeatured: vi.fn(),
  },
  OptimizeService: {
    listBuildSummaries: vi.fn(),
    markChangeReviewed: vi.fn(),
    listLoadoutRanks: vi.fn(),
  },
  SavesService: {
    listProfiles: vi.fn(),
    getProfileLoadouts: vi.fn(),
  },
}))

const { Route } = await import("../builds")
const BuildsPage = (Route as unknown as { component: () => React.ReactElement })
  .component

function renderPage() {
  return render(<BuildsPage />)
}

describe("builds list — saved-loadout rank badge", () => {
  beforeEach(() => {
    mockRanks = []
    localStorage.clear()
  })
  afterEach(cleanup)

  it("names the saved loadout and its rank when it is the top suggestion", () => {
    mockRanks = [
      {
        build_id: "build-1",
        rank: 1,
        total: 10,
        loadout_index: 0,
        loadout_name: "Dregs Raider",
      },
    ]
    renderPage()
    expect(
      screen.getByText("Saved: Dregs Raider · #1 of 10"),
    ).toBeInTheDocument()
  })

  it("reads identically further down the list", () => {
    mockRanks = [
      {
        build_id: "build-1",
        rank: 3,
        total: 10,
        loadout_index: 0,
        loadout_name: "Dregs Raider",
      },
    ]
    renderPage()
    // Same wording as rank 1, by design: the optimizer SUGGESTS arrangements
    // and the player may well prefer #3, so the badge states a position rather
    // than grading the save against the top result.
    expect(
      screen.getByText("Saved: Dregs Raider · #3 of 10"),
    ).toBeInTheDocument()
    expect(screen.queryByText(/In sync/)).not.toBeInTheDocument()
  })

  it("puts the badge on the ranked build only", () => {
    mockRanks = [
      {
        build_id: "build-1",
        rank: 2,
        total: 10,
        loadout_index: 0,
        loadout_name: "Dregs Raider",
      },
    ]
    renderPage()
    // Two build cards, exactly one badge.
    expect(screen.getAllByRole("textbox")).toHaveLength(2)
    expect(screen.getAllByTitle(/in-game loadout/)).toHaveLength(1)
  })

  it("says nothing when no loadout matches any of a build's results", () => {
    mockRanks = []
    renderPage()
    expect(screen.queryByTitle(/in-game loadout/)).not.toBeInTheDocument()
  })

  it("says a tie out loud instead of implying a clean rank", () => {
    // The optimizer breaks score ties arbitrarily, so several suggestions can
    // be exactly as good as the one the player saved. The server ranks them
    // together; the badge names the tie so "#1" isn't read as "the only one".
    mockRanks = [
      {
        build_id: "build-1",
        rank: 1,
        total: 10,
        tied: 3,
        loadout_index: 0,
        loadout_name: "Dregs Raider",
      },
    ]
    renderPage()
    expect(
      screen.getByText("Saved: Dregs Raider · #1 of 10 (tied)"),
    ).toBeInTheDocument()
    expect(
      screen.getByTitle(
        /ties for suggestion #1 of 10 .*3 results score the same/,
      ),
    ).toBeInTheDocument()
  })

  it("marks no tie when the rank stands alone", () => {
    mockRanks = [
      {
        build_id: "build-1",
        rank: 2,
        total: 10,
        tied: 1,
        loadout_index: 0,
        loadout_name: "Dregs Raider",
      },
    ]
    renderPage()
    expect(
      screen.getByText("Saved: Dregs Raider · #2 of 10"),
    ).toBeInTheDocument()
  })

  it("names an unnamed preset instead of rendering an empty badge", () => {
    mockRanks = [
      {
        build_id: "build-2",
        rank: 1,
        total: 10,
        loadout_index: 4,
        loadout_name: "",
      },
    ]
    renderPage()
    expect(screen.getByText("Saved: (unnamed) · #1 of 10")).toBeInTheDocument()
  })
})
