/**
 * Background tracker for the post-upload save re-optimization.
 *
 * Uploading a save kicks off an SSE stream that re-optimizes every build the new
 * inventory affects — up to a couple of minutes. We don't want that tied to the
 * /upload component's lifetime: the user should be free to navigate away and watch
 * progress from the navbar while it runs.
 *
 * So the whole stream lives HERE, in module scope (mirrors lib/pendingChanges.ts):
 * one in-flight job + a listener Set + useSyncExternalStore hooks. Because the fetch
 * loop is owned by the module, not a component, it keeps reading regardless of which
 * route is mounted. State is in-memory only (NOT sessionStorage, unlike
 * pendingChanges) — an in-flight SSE can't be resumed after a full reload, so
 * persisting a half-done job would only mislead. Builds that already finished keep
 * their server-side snapshots regardless.
 */
import { useSyncExternalStore } from "react"
import { toast } from "sonner"

import type { BuildChange } from "@/client"
import { queryClient } from "@/lib/queryClient"

export interface StreamUploadProgress {
  phase: "parsing" | "optimizing" | "done"
  buildIndex?: number
  buildTotal?: number
  buildName?: string
  vessel?: number
  vesselTotal?: number
  vesselName?: string
}

export interface StreamUploadResult {
  profiles: Array<{
    slot_index: number
    name: string
    relic_count: number
    id?: string
  }>
  profileCount: number
  platform: string
  relicDelta?: { added: number; removed: number }
  /** Whether this save was diffed against the previous one, and why not. */
  comparison?: {
    compared: boolean
    reason: string
    restarted_slots: number[]
  }
  changes: BuildChange[]
}

export type BuildJobStatus = "optimizing" | "done" | "error"

/** Per-build live progress, surfaced on the navbar tracker and the builds page. */
export interface BuildJobInfo {
  status: BuildJobStatus
  name?: string
  /** Populated on completion (the optimize_done change), for the summary list. */
  change?: BuildChange
}

export interface OptimizeJob {
  status: "parsing" | "optimizing" | "done" | "error"
  fileName: string
  progress: StreamUploadProgress
  /** Keyed by build_id (the backend tags every optimize_* SSE event with it). */
  builds: Record<string, BuildJobInfo>
  result?: StreamUploadResult
  error?: string
}

// --- store -----------------------------------------------------------------

let job: OptimizeJob | null = null
const listeners = new Set<() => void>()

function emit() {
  for (const l of listeners) l()
}

function setJob(next: OptimizeJob | null) {
  job = next
  emit()
}

function patchJob(patch: Partial<OptimizeJob>) {
  if (!job) return
  job = { ...job, ...patch }
  emit()
}

function setBuild(buildId: string, info: Partial<BuildJobInfo>) {
  if (!job) return
  const prev = job.builds[buildId]
  const next: BuildJobInfo = {
    status: info.status ?? prev?.status ?? "optimizing",
    name: info.name ?? prev?.name,
    change: info.change ?? prev?.change,
  }
  patchJob({ builds: { ...job.builds, [buildId]: next } })
}

function subscribe(cb: () => void) {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

// --- SSE reader (moved from upload.tsx) -------------------------------------

/**
 * Raw-fetch reader for POST /saves/upload/stream. Kept as a hand-rolled fetch
 * (not the generated client) because the generated client buffers the whole body,
 * and SSE needs incremental reads. Reports linear progress via `onProgress` and
 * per-build status via `onBuildEvent` (the backend sends `build_id` on every
 * optimize_start / optimize_progress / optimize_done event).
 */
async function runUploadStream(
  file: File,
  onProgress: (p: StreamUploadProgress) => void,
  onBuildEvent: (buildId: string, info: Partial<BuildJobInfo>) => void,
  onUploadComplete: () => void,
): Promise<StreamUploadResult> {
  const token = localStorage.getItem("access_token")
  const formData = new FormData()
  formData.append("file", file)

  const headers: HeadersInit = {}
  if (token)
    (headers as Record<string, string>).Authorization = `Bearer ${token}`

  const response = await fetch("/api/v1/saves/upload/stream", {
    method: "POST",
    headers,
    body: formData,
  })

  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: "Upload failed" }))
    throw new Error(err.detail ?? "Upload failed")
  }

  const reader = response.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  let uploadData: any = null
  const changes: BuildChange[] = []

  // Accumulate progress so per-build context (which build, of how many) survives
  // into the per-vessel ticks — the backend sends build identity only on
  // `optimize_start`, not on each `optimize_progress`.
  let progress: StreamUploadProgress = { phase: "parsing" }
  const emitProgress = (patch: Partial<StreamUploadProgress>) => {
    progress = { ...progress, ...patch }
    onProgress(progress)
  }

  const finalResult = (): StreamUploadResult => ({
    profiles: uploadData?.profiles ?? [],
    profileCount: uploadData?.profile_count ?? 0,
    platform: uploadData?.platform ?? "PC",
    relicDelta: uploadData?.relic_delta,
    comparison: uploadData?.comparison,
    changes,
  })

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    const parts = buffer.split("\n\n")
    buffer = parts.pop() ?? ""

    for (const part of parts) {
      const dataLine = part.split("\n").find((l) => l.startsWith("data: "))
      if (!dataLine) continue
      const payload = JSON.parse(dataLine.slice(6))

      if (payload.type === "upload_complete") {
        uploadData = payload.data
        emitProgress({ phase: "optimizing" })
        // The parsed inventory, loadouts, and profiles are already committed
        // server-side by the time this event fires — well before the slow
        // optimization phase. Let the caller refresh the save-file-backed views
        // now instead of waiting for the whole stream to finish.
        onUploadComplete()
      } else if (payload.type === "optimize_start") {
        if (payload.build_id)
          onBuildEvent(payload.build_id, {
            status: "optimizing",
            name: payload.build_name,
          })
        // New build: set its identity, clear the prior build's vessel sub-progress.
        emitProgress({
          buildIndex: payload.index,
          buildTotal: payload.total,
          buildName: payload.build_name,
          vessel: undefined,
          vesselTotal: undefined,
          vesselName: undefined,
        })
      } else if (payload.type === "optimize_progress") {
        // Vessel tick: merges onto the current build's identity from optimize_start.
        emitProgress({
          vessel: payload.vessel,
          vesselTotal: payload.total,
          vesselName: payload.name,
        })
      } else if (payload.type === "optimize_done") {
        // A failed build streams an ad-hoc `{status: "error"}` dict, not a real
        // BuildChange — read the raw status off the untyped payload to catch it.
        const failed = payload.change?.status === "error"
        const change = payload.change as BuildChange | undefined
        if (payload.build_id)
          onBuildEvent(payload.build_id, {
            status: failed ? "error" : "done",
            name: change?.build_name,
            change,
          })
        if (change) changes.push(change)
      } else if (payload.type === "complete") {
        emitProgress({ phase: "done" })
        return finalResult()
      }
    }
  }

  if (uploadData) return finalResult()

  throw new Error("Stream ended without completion")
}

// --- public API ------------------------------------------------------------

/**
 * Bump on every startUpload so a superseded stream's late callbacks no-op
 * (the latest upload always wins; the old fetch keeps running but can't write).
 */
let generation = 0

/**
 * Start (or restart) the background save upload + re-optimization. Owns the whole
 * stream lifecycle: drives the store, then invalidates queries and toasts on
 * completion — so those fire even if the user has navigated away from /upload.
 * Returns the underlying promise (callers may ignore it).
 */
export function startUpload(file: File): Promise<void> {
  const myGen = ++generation
  setJob({
    status: "parsing",
    fileName: file.name,
    progress: { phase: "parsing" },
    builds: {},
  })

  return runUploadStream(
    file,
    (p) => {
      if (myGen !== generation) return
      patchJob({
        status: p.phase === "parsing" ? "parsing" : "optimizing",
        progress: p,
      })
    },
    (buildId, info) => {
      if (myGen !== generation) return
      setBuild(buildId, info)
    },
    () => {
      if (myGen !== generation) return
      // Inventory, game loadouts, and profiles are persisted server-side the
      // moment parsing finishes — well before optimization completes. Refresh
      // those save-file-backed views right away so they don't show stale data
      // while the (slow) re-optimization keeps running in the background.
      queryClient.invalidateQueries({ queryKey: ["profiles"] })
      queryClient.invalidateQueries({ queryKey: ["save-status"] })
      queryClient.invalidateQueries({ queryKey: ["relics"] })
      queryClient.invalidateQueries({ queryKey: ["loadouts"] })
    },
  )
    .then((result) => {
      if (myGen !== generation) return
      patchJob({ status: "done", progress: { phase: "done" }, result })

      // Optimization-derived data is only valid once the whole stream completes.
      // (Inventory / profiles / loadouts were already refreshed at
      // upload_complete, above.)
      queryClient.invalidateQueries({ queryKey: ["builds"] })
      queryClient.invalidateQueries({ queryKey: ["snapshot"] })
      queryClient.invalidateQueries({ queryKey: ["build-summaries"] })

      const meaningful = result.changes.filter(
        (c) => c.status !== "unchanged" && c.status !== "new",
      )
      const description =
        meaningful.length > 0
          ? `Save imported — ${meaningful.length} build${
              meaningful.length !== 1 ? "s" : ""
            } re-optimized.`
          : `Save imported — ${result.profileCount} profile${
              result.profileCount !== 1 ? "s" : ""
            } found.`
      toast.success("Success!", { description })
    })
    .catch((err) => {
      if (myGen !== generation) return
      const message = err instanceof Error ? err.message : "Upload failed"
      patchJob({ status: "error", error: message })
      toast.error("Something went wrong!", { description: message })
    })
}

/** Clear a finished job from the tracker. Never kills an in-flight stream. */
export function dismissJob(): void {
  if (job && (job.status === "done" || job.status === "error")) {
    // Bump the generation so the (already-settled) stream's callbacks can't
    // resurrect a dismissed job in the unlikely event one is still pending.
    generation += 1
    setJob(null)
  }
}

/** Non-reactive read (for tests / imperative call sites). */
export function getJob(): OptimizeJob | null {
  return job
}

// --- hooks -----------------------------------------------------------------

/** Reactive snapshot of the active job (null when idle). */
export function useOptimizeJob(): OptimizeJob | null {
  return useSyncExternalStore(subscribe, () => job)
}

/** Reactive per-build optimize status, for lighting up build cards. */
export function useBuildOptimizeStatus(
  buildId: string,
): BuildJobStatus | undefined {
  const snap = useSyncExternalStore(subscribe, () => job)
  return snap?.builds[buildId]?.status
}
