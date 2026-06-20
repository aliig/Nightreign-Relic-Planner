/**
 * Unit tests for the background optimize-job store (optimizeJobs.ts).
 *
 * We drive the real SSE reader by stubbing global fetch with a fake streaming
 * Response (body.getReader() yields canned chunks), then assert the module store
 * transitions. queryClient + sonner are mocked so we can assert the post-completion
 * hand-off without a real client or toast UI.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/lib/queryClient", () => ({
  queryClient: { invalidateQueries: vi.fn() },
}))
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

import { toast } from "sonner"
import { queryClient } from "@/lib/queryClient"
import { getJob, startUpload } from "./optimizeJobs"

// ── helpers ────────────────────────────────────────────────────────────────

const sse = (obj: unknown) => `data: ${JSON.stringify(obj)}\n\n`
const enc = (s: string) => new TextEncoder().encode(s)

/** A fetch stub whose response streams the given byte chunks, in order. */
function fetchStreaming(chunks: Uint8Array[]) {
  return vi.fn(async () => {
    let i = 0
    return {
      ok: true,
      body: {
        getReader: () => ({
          read: async () =>
            i < chunks.length
              ? { done: false, value: chunks[i++] }
              : { done: true, value: undefined },
        }),
      },
    }
  })
}

function file() {
  return new File([new Uint8Array([0])], "NR0000.sl2")
}

const flush = () => new Promise((r) => setTimeout(r, 0))

// ── tests ────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks()
})
afterEach(() => {
  vi.unstubAllGlobals()
})

describe("startUpload — happy path", () => {
  it("drives the job to done with per-build status and a result", async () => {
    const full =
      sse({
        type: "upload_complete",
        data: {
          profiles: [{ slot_index: 0, name: "Wylder", relic_count: 5 }],
          profile_count: 1,
          platform: "PC",
          relic_delta: { added: 2, removed: 0 },
        },
      }) +
      sse({
        type: "optimize_start",
        build_id: "b1",
        build_name: "Fire Build",
        index: 1,
        total: 2,
      }) +
      sse({
        type: "optimize_progress",
        build_id: "b1",
        vessel: 1,
        total: 3,
        name: "Vessel A",
      }) +
      sse({
        type: "optimize_done",
        build_id: "b1",
        change: {
          build_id: "b1",
          build_name: "Fire Build",
          slot_index: 0,
          status: "improved",
          best_before: 100,
          best_after: 150,
          delta: 50,
          reliable: true,
        },
      }) +
      sse({
        type: "optimize_start",
        build_id: "b2",
        build_name: "Ice Build",
        index: 2,
        total: 2,
      }) +
      sse({
        type: "optimize_done",
        build_id: "b2",
        change: {
          build_id: "b2",
          build_name: "Ice Build",
          slot_index: 0,
          status: "unchanged",
          reliable: true,
        },
      }) +
      sse({ type: "complete", changes: [] })

    // Split the bytes mid-stream so the reader's buffer-splitting is exercised.
    const bytes = enc(full)
    const mid = Math.floor(bytes.length / 2)
    vi.stubGlobal(
      "fetch",
      fetchStreaming([bytes.slice(0, mid), bytes.slice(mid)]),
    )

    await startUpload(file())

    const j = getJob()
    expect(j?.status).toBe("done")
    expect(j?.builds.b1).toMatchObject({ status: "done", name: "Fire Build" })
    expect(j?.builds.b1.change?.status).toBe("improved")
    expect(j?.builds.b2.status).toBe("done")
    expect(j?.result?.profileCount).toBe(1)
    expect(j?.result?.changes.length).toBe(2)
  })

  it("refreshes the relevant queries and toasts on completion", async () => {
    const full =
      sse({
        type: "upload_complete",
        data: { profiles: [], profile_count: 1, platform: "PC" },
      }) + sse({ type: "complete", changes: [] })
    vi.stubGlobal("fetch", fetchStreaming([enc(full)]))

    await startUpload(file())

    for (const key of [
      "profiles",
      "save-status",
      "builds",
      "snapshot",
      "build-summaries",
    ]) {
      expect(queryClient.invalidateQueries).toHaveBeenCalledWith({
        queryKey: [key],
      })
    }
    expect(toast.success).toHaveBeenCalled()
  })
})

describe("startUpload — failures and interim state", () => {
  it("captures a failed upload as an error job", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        json: async () => ({ detail: "bad save" }),
      })),
    )

    await startUpload(file())

    expect(getJob()?.status).toBe("error")
    expect(getJob()?.error).toBe("bad save")
    expect(toast.error).toHaveBeenCalled()
  })

  it("marks a build optimizing before it completes", async () => {
    const part1 = enc(
      sse({
        type: "upload_complete",
        data: { profiles: [], profile_count: 0, platform: "PC" },
      }) +
        sse({
          type: "optimize_start",
          build_id: "b1",
          build_name: "Fire Build",
          index: 1,
          total: 1,
        }) +
        sse({
          type: "optimize_progress",
          build_id: "b1",
          vessel: 1,
          total: 2,
          name: "V",
        }),
    )
    const part2 = enc(
      sse({
        type: "optimize_done",
        build_id: "b1",
        change: {
          build_id: "b1",
          build_name: "Fire Build",
          slot_index: 0,
          status: "improved",
          best_before: 1,
          best_after: 2,
          reliable: true,
        },
      }) + sse({ type: "complete", changes: [] }),
    )

    let release: () => void = () => {}
    const gate = new Promise<void>((r) => {
      release = r
    })
    let i = 0
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        body: {
          getReader: () => ({
            read: async () => {
              if (i === 0) {
                i++
                return { done: false, value: part1 }
              }
              if (i === 1) {
                i++
                await gate
                return { done: false, value: part2 }
              }
              return { done: true, value: undefined }
            },
          }),
        },
      })),
    )

    const p = startUpload(file())
    await flush() // let part1 process; the reader is now parked on the gate
    expect(getJob()?.status).toBe("optimizing")
    expect(getJob()?.builds.b1.status).toBe("optimizing")

    release()
    await p
    expect(getJob()?.builds.b1.status).toBe("done")
  })
})
