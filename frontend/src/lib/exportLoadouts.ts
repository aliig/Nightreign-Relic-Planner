/**
 * Raw-fetch client for POST /api/v1/saves/export-loadouts.
 *
 * Like exportSave.ts, the generated SDK can't stream a binary .sl2 response, so
 * we fetch directly to read the Blob + summary headers and trigger a download.
 *
 * A LoadoutOp is one edit to the in-game relic-loadout presets of a save slot.
 * The backend applies a batch deterministically (renames/overwrites, then deletes
 * in descending index order, then adds; resets touch independent regions).
 */
export type LoadoutOp =
  | { op: "reset_vessels" }
  | { op: "reset_presets" }
  | { op: "delete"; index: number }
  | { op: "rename"; index: number; name: string }
  | {
      op: "overwrite"
      index: number
      character: string
      vessel_id: number
      ga_handles: number[]
      name?: string
    }
  | {
      op: "add"
      character: string
      vessel_id: number
      ga_handles: number[]
      name: string
    }

export type LoadoutExportResult = {
  filename: string
  added: number
  deleted: number
  renamed: number
  overwritten: number
  vesselsReset: boolean
  presetsReset: boolean
  used: number
}

export class LoadoutExportError extends Error {}

export async function exportModifiedLoadouts(params: {
  file: File
  slotIndex: number
  operations: LoadoutOp[]
}): Promise<LoadoutExportResult> {
  const { file, slotIndex, operations } = params
  const token = localStorage.getItem("access_token")

  const form = new FormData()
  form.append("file", file)
  form.append("slot_index", String(slotIndex))
  form.append("operations", JSON.stringify(operations))

  const res = await fetch("/api/v1/saves/export-loadouts", {
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
    throw new LoadoutExportError(message)
  }

  const blob = await res.blob()
  const cd = res.headers.get("content-disposition") ?? ""
  const match = cd.match(/filename="?([^"]+)"?/)
  const filename = match?.[1] ?? "edited.sl2"

  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)

  return {
    filename,
    added: Number(res.headers.get("x-loadouts-added") ?? 0),
    deleted: Number(res.headers.get("x-loadouts-deleted") ?? 0),
    renamed: Number(res.headers.get("x-loadouts-renamed") ?? 0),
    overwritten: Number(res.headers.get("x-loadouts-overwritten") ?? 0),
    vesselsReset: res.headers.get("x-vessels-reset") === "1",
    presetsReset: res.headers.get("x-presets-reset") === "1",
    used: Number(res.headers.get("x-loadouts-used") ?? 0),
  }
}
