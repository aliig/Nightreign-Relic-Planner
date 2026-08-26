/**
 * SSE reader for POST /optimize/stream — one build, streaming vessel progress.
 *
 * Lives in lib/ rather than beside the results table because two very different
 * callers need it: the optimize page (a mounted component) and the background
 * bulk job in lib/optimizeJobs.ts, which must not pull the component tree into
 * a module store. OptimizeResults re-exports it so existing imports still work.
 *
 * Hand-rolled fetch, not the generated client, for the same reason as the upload
 * stream: the generated client buffers the whole body, and SSE needs incremental
 * reads.
 */
import type { BuildChange, VesselResult } from "@/client"

export interface OptimizeProgress {
  vessel: number
  total: number
  name: string
}

export async function runOptimizeStream(
  requestBody: Record<string, unknown>,
  onProgress: (p: OptimizeProgress) => void,
  onChange?: (change: BuildChange | null) => void,
): Promise<VesselResult[]> {
  const token = localStorage.getItem("access_token")
  const headers: HeadersInit = { "Content-Type": "application/json" }
  if (token)
    (headers as Record<string, string>).Authorization = `Bearer ${token}`

  const response = await fetch("/api/v1/optimize/stream", {
    method: "POST",
    headers,
    body: JSON.stringify(requestBody),
  })

  if (!response.ok) {
    const err = await response
      .json()
      .catch(() => ({ detail: "Optimization failed" }))
    throw new Error(err.detail ?? "Optimization failed")
  }

  const reader = response.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // SSE events are separated by double newlines
    const parts = buffer.split("\n\n")
    buffer = parts.pop() ?? ""

    for (const part of parts) {
      const dataLine = part.split("\n").find((l) => l.startsWith("data: "))
      if (!dataLine) continue
      const payload = JSON.parse(dataLine.slice(6))

      if (payload.type === "progress") {
        onProgress({
          vessel: payload.vessel,
          total: payload.total,
          name: payload.name,
        })
      } else if (payload.type === "result") {
        onChange?.((payload.change ?? null) as BuildChange | null)
        return payload.data as VesselResult[]
      } else if (payload.type === "error") {
        throw new Error(payload.detail ?? "Optimization failed")
      }
    }
  }

  throw new Error("Stream ended without a result")
}
