/**
 * Unit tests for the builds page's "your builds are out of date" affordance.
 *
 * The app re-optimizes on two triggers of its own — uploading a save, and
 * viewing one build — and neither covers a Relic Rites spree, which changes the
 * inventory EVERY build is scored against at once. Before this, the page kept
 * showing each build's pre-purchase verdict with nothing to say it was stale
 * and no way to bring the library current short of opening each build.
 *
 * These pin that contract: the count is honest, the button re-runs exactly the
 * stale builds with the staged diff attached, a stale verdict is labelled
 * rather than passed off as current, and a re-run is still one click away when
 * nothing is stale.
 *
 * Harness mirrors builds-loadout-rank.test.tsx: mock createFileRoute so the
 * page component is reachable, and answer react-query by query key.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type { BuildFreshness, BuildSnapshotSummary } from "@/client"
import type { OptimizeJob } from "@/lib/optimizeJobs"

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

let mockFreshness: BuildFreshness[] | undefined = []
let mockSummaries: BuildSnapshotSummary[] = []
let mockJob: OptimizeJob | null = null

const startOptimizeAll = vi.fn()

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
      if (key[0] === "build-freshness") return { data: mockFreshness }
      if (key[0] === "build-summaries") return { data: mockSummaries }
      if (key[0] === "profiles")
        return {
          data: { data: [{ id: "profile-1", slot_index: 0, name: "Hero" }] },
        }
      if (key[0] === "loadouts") return { data: { data: [] } }
      if (key[0] === "loadout-ranks") return { data: [] }
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

vi.mock("@/lib/optimizeJobs", () => ({
  startOptimizeAll: (...args: unknown[]) => startOptimizeAll(...args),
  useOptimizeJob: () => mockJob,
  useBuildOptimizeStatus: () => undefined,
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
    listBuildFreshness: vi.fn(),
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

const fresh = (id: string, isFresh: boolean): BuildFreshness => ({
  build_id: id,
  fresh: isFresh,
})

/** A build that HAS been optimized before (a snapshot exists for it). */
const summary = (id: string): BuildSnapshotSummary =>
  ({ build_id: id, reviewed: true, best_score: 100 }) as BuildSnapshotSummary

describe("builds list — out-of-date builds", () => {
  beforeEach(() => {
    mockFreshness = []
    mockSummaries = []
    mockJob = null
    startOptimizeAll.mockClear()
    localStorage.clear()
  })
  afterEach(cleanup)

  it("counts the stale builds and offers to optimize exactly those", () => {
    mockFreshness = [fresh("build-1", false), fresh("build-2", true)]
    mockSummaries = [summary("build-1"), summary("build-2")]
    render(<BuildsPage />)

    expect(screen.getByText("1 build out of date")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: /Optimize 1 build/ }))

    expect(startOptimizeAll).toHaveBeenCalledTimes(1)
    const arg = startOptimizeAll.mock.calls[0][0]
    expect(arg.profileId).toBe("profile-1")
    // Only the stale one: re-running a build whose inputs have not moved is
    // pure cost, and the relevant-subset gate exists precisely to skip it.
    expect(arg.builds).toEqual([{ id: "build-1", name: "Dregs Raider" }])
  })

  it("pluralizes and targets every stale build", () => {
    mockFreshness = [fresh("build-1", false), fresh("build-2", false)]
    mockSummaries = [summary("build-1"), summary("build-2")]
    render(<BuildsPage />)

    expect(screen.getByText("2 builds out of date")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: /Optimize 2 builds/ }))
    expect(startOptimizeAll.mock.calls[0][0].builds).toHaveLength(2)
  })

  it("still offers a re-run when everything is current", () => {
    mockFreshness = [fresh("build-1", true), fresh("build-2", true)]
    render(<BuildsPage />)

    expect(
      screen.getByText("All builds are up to date with your current relics."),
    ).toBeInTheDocument()
    // Always available, per the brief: "a button that's always on that page".
    fireEvent.click(screen.getByRole("button", { name: /Re-optimize all/ }))
    expect(startOptimizeAll.mock.calls[0][0].builds).toHaveLength(2)
  })

  it("passes the staged diff so purchased relics are scored in", () => {
    mockFreshness = [fresh("build-1", false), fresh("build-2", true)]
    render(<BuildsPage />)
    fireEvent.click(screen.getByRole("button", { name: /Optimize 1 build/ }))
    // Nothing staged in this test, but the wire fields must still be present —
    // this is the channel a Relic Rites batch travels on.
    expect(startOptimizeAll.mock.calls[0][0].staged).toBeDefined()
  })

  it("does not accuse a never-optimized build of being out of date", () => {
    // Nothing has changed for a build that has never run — there is simply no
    // result yet, and saying "your relics have changed" would be a lie.
    mockFreshness = [fresh("build-1", false), fresh("build-2", false)]
    mockSummaries = []
    render(<BuildsPage />)

    expect(screen.getByText("2 builds not optimized yet")).toBeInTheDocument()
    expect(screen.queryByText(/out of date/)).not.toBeInTheDocument()
  })

  it("says builds need optimizing when the reasons are mixed", () => {
    mockFreshness = [fresh("build-1", false), fresh("build-2", false)]
    mockSummaries = [summary("build-1")]
    render(<BuildsPage />)

    expect(screen.getByText("2 builds need optimizing")).toBeInTheDocument()
  })

  it("labels a verdict that predates the current relics", () => {
    mockFreshness = [fresh("build-1", false), fresh("build-2", true)]
    mockSummaries = [
      {
        build_id: "build-1",
        reviewed: false,
        best_score: 150,
        change: {
          build_id: "build-1",
          build_name: "Dregs Raider",
          slot_index: 0,
          status: "improved",
          causes: ["relics"],
          best_before: 100,
          best_after: 150,
          reliable: true,
        },
      } as BuildSnapshotSummary,
    ]
    render(<BuildsPage />)

    // The stale verdict is still shown — it is the last thing we know — but it
    // must not read as current, which is the confusion that started all this.
    expect(screen.getByText("(out of date)")).toBeInTheDocument()
  })

  it("shows progress instead of the button while a run is in flight", () => {
    mockFreshness = [fresh("build-1", false), fresh("build-2", false)]
    mockJob = {
      kind: "rebuild",
      status: "optimizing",
      progress: { phase: "optimizing", buildIndex: 1, buildTotal: 2 },
      builds: { "build-1": { status: "done" } },
    }
    render(<BuildsPage />)

    expect(screen.getByText(/Optimizing 1 of 2/)).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /Optimize 2 builds/ }),
    ).not.toBeInTheDocument()
  })

  it("says nothing until the freshness answer arrives", () => {
    mockFreshness = undefined
    render(<BuildsPage />)
    // An unanswered query must not be read as "everything is stale" — that
    // would flash a bogus count on every page load — nor as "everything is
    // current", which the page has no basis to claim yet.
    expect(screen.queryByText(/out of date/)).not.toBeInTheDocument()
    expect(
      screen.queryByText(/All builds are up to date/),
    ).not.toBeInTheDocument()
    // The manual re-run stays available regardless.
    expect(
      screen.getByRole("button", { name: /Re-optimize all/ }),
    ).toBeInTheDocument()
  })
})
