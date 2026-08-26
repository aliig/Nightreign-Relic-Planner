/**
 * Regression tests for the Upload page's post-optimization results block.
 *
 * The background job (lib/optimizeJobs) is a *live stream* store: the navbar
 * tracker auto-clears a finished job a few seconds after it lands, and the Sheet
 * has a Dismiss button. The upload page used to render its results straight off
 * that store, so the whole "N builds updated" comparison vanished under the user
 * a few seconds after appearing. These tests pin the latch that fixes it.
 *
 * The job store is mocked with a plain mutable value: `useOptimizeJob` just
 * returns it, so a `rerender()` picks up whatever the test set — enough to
 * simulate the store going null out from under the page.
 */
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type { OptimizeJob, StreamUploadResult } from "@/lib/optimizeJobs"

// ── mocks (must be declared before the import under test) ──────────────────

let fakeJob: OptimizeJob | null = null

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@tanstack/react-router")>()
  return {
    ...mod,
    useNavigate: () => vi.fn(),
    Link: ({ children }: { children: React.ReactNode }) => (
      <a href="/">{children}</a>
    ),
    createFileRoute: () => (config: Record<string, unknown>) => config,
  }
})

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@tanstack/react-query")>()
  return {
    ...mod,
    useMutation: () => ({
      mutate: vi.fn(),
      isPending: false,
      isError: false,
      error: null,
    }),
    useQueryClient: () => ({ invalidateQueries: vi.fn() }),
  }
})

vi.mock("@/client", () => ({
  SavesService: { uploadSave: vi.fn() },
}))

vi.mock("@/hooks/useCustomToast", () => ({
  default: () => ({ showSuccessToast: vi.fn(), showErrorToast: vi.fn() }),
}))

vi.mock("@/utils", () => ({
  handleError: vi.fn(),
  formatRelativeTime: vi.fn(() => "just now"),
}))

vi.mock("@/hooks/useSaveStatus", () => ({
  useSaveStatus: () => ({ status: null, isLoading: false, isAnon: false }),
  storeAnonUploadMeta: vi.fn(),
}))

// The effect-name map is a game-data query; the change list renders without it.
vi.mock("@/hooks/useEffectMap", () => ({
  useEffectMap: () => new Map<number, string>(),
}))

vi.mock("@/lib/optimizeJobs", () => ({
  startUpload: vi.fn(),
  useOptimizeJob: () => fakeJob,
}))

// ── import after mocks ─────────────────────────────────────────────────────

import { Route } from "../upload"

const UploadPage = (Route as unknown as { component: React.FC }).component

// ── fixtures ──────────────────────────────────────────────────────────────

const RESULT: StreamUploadResult = {
  profiles: [{ slot_index: 0, name: "Wylder", relic_count: 12 }],
  profileCount: 1,
  platform: "PC",
  changes: [
    {
      build_id: "b1",
      build_name: "Bleed Wylder",
      slot_index: 0,
      status: "improved",
      best_before: 100,
      best_after: 130,
      entered: [{ real_id: 7, name: "Polished Burning Scene", color: "Red" }],
    } as StreamUploadResult["changes"][number],
  ],
}

function doneJob(): OptimizeJob {
  return {
    kind: "upload",
    status: "done",
    fileName: "NR0000.sl2",
    progress: { phase: "done" },
    builds: {},
    result: RESULT,
  }
}

beforeEach(() => {
  fakeJob = null
})

afterEach(cleanup)

// ── tests ─────────────────────────────────────────────────────────────────

describe("UploadPage — completed-job results", () => {
  it("renders the change summary and profile cards when the job finishes", () => {
    fakeJob = doneJob()
    render(<UploadPage />)

    expect(screen.getByText(/1 build updated/i)).toBeInTheDocument()
    expect(screen.getByText("Bleed Wylder")).toBeInTheDocument()
    expect(screen.getByText(/30% stronger/i)).toBeInTheDocument()
    expect(screen.getByText("Wylder")).toBeInTheDocument()
  })

  it("keeps showing them after the tracker clears the job", () => {
    fakeJob = doneJob()
    const { rerender } = render(<UploadPage />)
    expect(screen.getByText(/1 build updated/i)).toBeInTheDocument()

    // The navbar tracker's auto-clear (or Dismiss) empties the store.
    fakeJob = null
    rerender(<UploadPage />)

    // The comparison must survive — it is the page's own record of the upload.
    expect(screen.getByText(/1 build updated/i)).toBeInTheDocument()
    expect(screen.getByText("Bleed Wylder")).toBeInTheDocument()
    expect(screen.getByText("Wylder")).toBeInTheDocument()
  })
})
