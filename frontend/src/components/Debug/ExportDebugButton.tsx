import { Bug } from "lucide-react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import useAuth from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"

type ExportResponse = {
  written_path: string
  filename: string
  bundle: unknown
}

async function postExport(
  body: Record<string, unknown>,
): Promise<ExportResponse> {
  const token = localStorage.getItem("access_token")
  const res = await fetch("/api/v1/debug/export", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Export failed" }))
    throw new Error(err.detail ?? "Export failed")
  }
  return (await res.json()) as ExportResponse
}

/**
 * Admin-only "Export debug state" button. Posts the current scenario to the
 * local-only /debug/export endpoint, which writes a gitignored
 * debug-exports/latest.json bundle (for Claude Code to read + replay) and
 * returns it for a browser download. Renders nothing for non-superusers.
 */
export function ExportDebugButton({
  mode,
  buildId,
  profileId,
  results,
  label = "Export debug",
}: {
  mode: "view" | "full"
  buildId?: string
  profileId?: string
  results?: unknown
  label?: string
}) {
  const { user } = useAuth()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [busy, setBusy] = useState(false)

  // Local + superuser only; the endpoint is also unmounted outside local env.
  if (!user?.is_superuser) return null

  const onClick = async () => {
    setBusy(true)
    try {
      const out = await postExport({
        mode,
        build_id: buildId ?? null,
        profile_id: profileId ?? null,
        results: results ?? null,
      })
      // Also hand the user a downloadable copy.
      const blob = new Blob([JSON.stringify(out.bundle, null, 2)], {
        type: "application/json",
      })
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = out.filename
      a.click()
      URL.revokeObjectURL(url)
      showSuccessToast(`Wrote ${out.written_path}`)
    } catch (err) {
      showErrorToast(err instanceof Error ? err.message : "Export failed")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Button variant="outline" size="sm" onClick={onClick} disabled={busy}>
      <Bug className="h-3.5 w-3.5 mr-1" />
      {busy ? "Exporting…" : label}
    </Button>
  )
}
