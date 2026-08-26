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
import { getJob, startOptimizeAll, startUpload } from "./optimizeJobs"

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
      "relics",
      "loadouts",
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

  it("refreshes the save-file-backed views at upload_complete, before optimization finishes", async () => {
    // A reader that delivers upload_complete and then hangs — i.e. the (slow)
    // optimization phase is still running and the stream hasn't completed.
    const reader = {
      read: vi
        .fn()
        .mockResolvedValueOnce({
          done: false,
          value: enc(
            sse({
              type: "upload_complete",
              data: { profiles: [], profile_count: 1, platform: "PC" },
            }),
          ),
        })
        // Never resolves: optimization is still in flight.
        .mockImplementationOnce(() => new Promise<never>(() => {})),
    }
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, body: { getReader: () => reader } })),
    )

    // Don't await — the job never completes in this scenario.
    void startUpload(file())
    await flush()
    await flush()

    // Inventory / loadouts / profiles refreshed already…
    for (const key of ["profiles", "save-status", "relics", "loadouts"]) {
      expect(queryClient.invalidateQueries).toHaveBeenCalledWith({
        queryKey: [key],
      })
    }
    // …but the optimization-derived queries are NOT touched until the stream
    // completes (this is the bug being guarded against — they used to all wait).
    expect(queryClient.invalidateQueries).not.toHaveBeenCalledWith({
      queryKey: ["builds"],
    })
    expect(getJob()?.status).toBe("optimizing")
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

// ── startOptimizeAll ───────────────────────────────────────────────────────

/**
 * A fetch stub that serves one canned /optimize/stream response per call, in
 * order — the bulk job runs the builds sequentially, one request each.
 */
function fetchPerBuild(bodies: string[]) {
  let call = 0
  return vi.fn(async () => {
    const body = bodies[call++] ?? ""
    let sent = false
    return {
      ok: true,
      body: {
        getReader: () => ({
          read: async () => {
            if (sent) return { done: true, value: undefined }
            sent = true
            return { done: false, value: enc(body) }
          },
        }),
      },
    }
  })
}

const resultEvent = (buildId: string, status: string) =>
  sse({
    type: "result",
    data: [],
    change: {
      build_id: buildId,
      build_name: buildId,
      slot_index: 0,
      status,
      reliable: true,
    },
  })

const targets = [
  { id: "b1", name: "Fire Build" },
  { id: "b2", name: "Ice Build" },
]

describe("startOptimizeAll", () => {
  it("runs every target build and records each change", async () => {
    vi.stubGlobal(
      "fetch",
      fetchPerBuild([
        sse({ type: "progress", vessel: 1, total: 3, name: "Vessel A" }) +
          resultEvent("b1", "improved"),
        resultEvent("b2", "unchanged"),
      ]),
    )

    await startOptimizeAll({
      profileId: "p1",
      builds: targets,
      staged: { staged_sells: [1], staged_mints: [] },
    })

    const j = getJob()
    expect(j?.kind).toBe("rebuild")
    expect(j?.status).toBe("done")
    expect(j?.builds.b1).toMatchObject({ status: "done", name: "Fire Build" })
    expect(j?.builds.b1.change?.status).toBe("improved")
    expect(j?.builds.b2).toMatchObject({ status: "done", name: "Ice Build" })
  })

  it("sends the staged diff with every build, so purchases are scored in", async () => {
    const f = fetchPerBuild([resultEvent("b1", "improved")])
    vi.stubGlobal("fetch", f)

    const staged = { staged_sells: [7], staged_mints: [{ handle: -1 }] }
    await startOptimizeAll({
      profileId: "p1",
      builds: [targets[0]],
      staged,
    })

    const body = JSON.parse((f.mock.calls[0] as any[])[1].body)
    expect(body).toMatchObject({
      build_id: "b1",
      profile_id: "p1",
      staged_sells: [7],
      staged_mints: [{ handle: -1 }],
    })
  })

  it("keeps going when one build fails, and reports the failure", async () => {
    // Second response has no `result` event: the reader throws "stream ended".
    vi.stubGlobal("fetch", fetchPerBuild([resultEvent("b1", "improved"), ""]))

    await startOptimizeAll({
      profileId: "p1",
      builds: [targets[0], targets[1]],
      staged: {},
    })

    const j = getJob()
    expect(j?.status).toBe("done")
    expect(j?.builds.b1.status).toBe("done")
    expect(j?.builds.b2.status).toBe("error")
    expect(toast.error).toHaveBeenCalled()
  })

  it("refreshes the builds, snapshot and freshness reads on completion", async () => {
    vi.stubGlobal("fetch", fetchPerBuild([resultEvent("b1", "improved")]))

    await startOptimizeAll({
      profileId: "p1",
      builds: [targets[0]],
      staged: {},
    })

    const keys = (queryClient.invalidateQueries as any).mock.calls.map(
      (c: any[]) => c[0].queryKey[0],
    )
    // Without the freshness invalidation the banner would keep claiming the
    // builds are out of date after they were just brought current.
    expect(keys).toContain("build-freshness")
    expect(keys).toContain("build-summaries")
    expect(keys).toContain("snapshot")
    expect(toast.success).toHaveBeenCalled()
  })

  it("reports live progress while a build streams", async () => {
    // Hold the stream open so the job is genuinely mid-run when we look:
    // completion resets progress to {phase:"done"}, which would race a plain
    // microtask flush.
    let release!: () => void
    const held = new Promise<void>((r) => {
      release = r
    })
    let sent = false
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        body: {
          getReader: () => ({
            read: async () => {
              if (sent) return { done: true, value: undefined }
              await held
              sent = true
              return {
                done: false,
                value: enc(
                  sse({
                    type: "progress",
                    vessel: 2,
                    total: 5,
                    name: "Vessel B",
                  }) + resultEvent("b1", "improved"),
                ),
              }
            },
          }),
        },
      })),
    )

    const p = startOptimizeAll({
      profileId: "p1",
      builds: [targets[0]],
      staged: {},
    })
    await flush()

    const mid = getJob()
    expect(mid?.status).toBe("optimizing")
    expect(mid?.progress.buildTotal).toBe(1)
    expect(mid?.progress.buildIndex).toBe(1)
    expect(mid?.progress.buildName).toBe("Fire Build")
    expect(mid?.builds.b1.status).toBe("optimizing")

    release()
    await p
    expect(getJob()?.builds.b1.status).toBe("done")
  })
})
