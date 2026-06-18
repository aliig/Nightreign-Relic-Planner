/**
 * Raw-fetch client for POST /api/v1/saves/export.
 *
 * The generated SDK buffers/JSON-decodes responses, which doesn't fit a binary
 * .sl2 download — so we use fetch directly (same pattern as the SSE endpoints
 * and ExportDebugButton) to read the Blob and the summary headers, then trigger
 * a browser download.
 */
export type ExportResult = {
  filename: string
  removed: number
  favoritesChanged: number
  murkCredit: number
  murksTotal: number
}

export class ExportError extends Error {}

export async function exportModifiedSave(params: {
  file: File
  slotIndex: number
  gaHandles: number[]
  favoriteChanges: Record<number, boolean>
}): Promise<ExportResult> {
  const { file, slotIndex, gaHandles, favoriteChanges } = params
  const token = localStorage.getItem("access_token")

  const form = new FormData()
  form.append("file", file)
  form.append("slot_index", String(slotIndex))
  form.append("ga_handles", JSON.stringify(gaHandles))
  form.append("favorite_changes", JSON.stringify(favoriteChanges))

  const res = await fetch("/api/v1/saves/export", {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  })

  if (!res.ok) {
    let detail: unknown = "Export failed"
    try {
      detail = (await res.json()).detail
    } catch {
      /* keep default */
    }
    const message =
      typeof detail === "string"
        ? detail
        : ((detail as { message?: string })?.message ?? "Export failed")
    throw new ExportError(message)
  }

  const blob = await res.blob()
  const cd = res.headers.get("content-disposition") ?? ""
  const match = cd.match(/filename="?([^"]+)"?/)
  const filename = match?.[1] ?? "edited.sl2"

  // Trigger the download.
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)

  return {
    filename,
    removed: Number(res.headers.get("x-relics-removed") ?? 0),
    favoritesChanged: Number(res.headers.get("x-favorites-changed") ?? 0),
    murkCredit: Number(res.headers.get("x-murks-credited") ?? 0),
    murksTotal: Number(res.headers.get("x-murks-total") ?? 0),
  }
}
