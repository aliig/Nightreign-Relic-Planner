/**
 * One-shot export of the whole pending-changes cart.
 *
 * Applies relic edits (sell/bookmark) and loadout edits (add/delete/rename/
 * overwrite/resets) to the in-session save and downloads a single modified .sl2.
 * The two backend endpoints are chained client-side: the relic export's output
 * blob is fed as the input to the loadout export, so one download carries every
 * change for a slot. Repeats per slot that has pending changes.
 */
import type { PendingLoadoutOp, SlotPending } from "./pendingChanges"

export type PendingExportSummary = {
  filename: string
  sold: number
  bookmarks: number
  added: number
  deleted: number
  renamed: number
  overwritten: number
  vesselsReset: boolean
  presetsReset: boolean
}

export class PendingExportError extends Error {}

type BackendOp =
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

function toBackendOp(op: PendingLoadoutOp): BackendOp {
  switch (op.kind) {
    case "add":
      return {
        op: "add",
        character: op.character,
        vessel_id: op.vessel_id,
        ga_handles: op.ga_handles,
        name: op.name,
      }
    case "overwrite":
      return {
        op: "overwrite",
        index: op.index,
        character: op.character,
        vessel_id: op.vessel_id,
        ga_handles: op.ga_handles,
        name: op.name,
      }
    case "delete":
      return { op: "delete", index: op.index }
    case "rename":
      return { op: "rename", index: op.index, name: op.name }
    case "reset_vessels":
      return { op: "reset_vessels" }
    case "reset_presets":
      return { op: "reset_presets" }
  }
}

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem("access_token")
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function detailMessage(res: Response): Promise<string> {
  let detail: unknown = "Export failed"
  try {
    detail = (await res.json()).detail
  } catch {
    /* keep default */
  }
  return typeof detail === "string"
    ? detail
    : ((detail as { message?: string })?.message ?? "Export failed")
}

function filenameFrom(res: Response, fallback: string): string {
  const cd = res.headers.get("content-disposition") ?? ""
  return cd.match(/filename="?([^"]+)"?/)?.[1] ?? fallback
}

/** POST /saves/export — returns the modified blob + summary headers (no download). */
async function postRelicExport(
  file: Blob,
  filename: string,
  slotIndex: number,
  sells: number[],
  favorites: Record<number, boolean>,
): Promise<{ blob: Blob; filename: string; headers: Headers }> {
  const form = new FormData()
  form.append("file", file, filename)
  form.append("slot_index", String(slotIndex))
  form.append("ga_handles", JSON.stringify(sells))
  form.append("favorite_changes", JSON.stringify(favorites))
  const res = await fetch("/api/v1/saves/export", {
    method: "POST",
    headers: authHeaders(),
    body: form,
  })
  if (!res.ok) throw new PendingExportError(await detailMessage(res))
  return {
    blob: await res.blob(),
    filename: filenameFrom(res, filename),
    headers: res.headers,
  }
}

/** POST /saves/export-loadouts — returns the modified blob + summary headers. */
async function postLoadoutExport(
  file: Blob,
  filename: string,
  slotIndex: number,
  operations: BackendOp[],
): Promise<{ blob: Blob; filename: string; headers: Headers }> {
  const form = new FormData()
  form.append("file", file, filename)
  form.append("slot_index", String(slotIndex))
  form.append("operations", JSON.stringify(operations))
  const res = await fetch("/api/v1/saves/export-loadouts", {
    method: "POST",
    headers: authHeaders(),
    body: form,
  })
  if (!res.ok) throw new PendingExportError(await detailMessage(res))
  return {
    blob: await res.blob(),
    filename: filenameFrom(res, filename),
    headers: res.headers,
  }
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

/**
 * Apply every pending change (across all slots that have any) to ``file`` and
 * download the single resulting .sl2.
 */
export async function exportPendingChanges(
  file: File,
  pending: Record<number, SlotPending>,
): Promise<PendingExportSummary> {
  let current: Blob = file
  let filename = file.name || "save.sl2"
  const sum: PendingExportSummary = {
    filename,
    sold: 0,
    bookmarks: 0,
    added: 0,
    deleted: 0,
    renamed: 0,
    overwritten: 0,
    vesselsReset: false,
    presetsReset: false,
  }

  for (const [slotStr, slot] of Object.entries(pending)) {
    const slotIndex = Number(slotStr)

    if (slot.sells.length || Object.keys(slot.favorites).length) {
      const r = await postRelicExport(
        current,
        filename,
        slotIndex,
        slot.sells,
        slot.favorites,
      )
      current = r.blob
      filename = r.filename
      sum.sold += Number(r.headers.get("x-relics-removed") ?? 0)
      sum.bookmarks += Number(r.headers.get("x-favorites-changed") ?? 0)
    }

    const ops = slot.loadoutOps.map(toBackendOp)
    if (ops.length) {
      const r = await postLoadoutExport(current, filename, slotIndex, ops)
      current = r.blob
      filename = r.filename
      sum.added += Number(r.headers.get("x-loadouts-added") ?? 0)
      sum.deleted += Number(r.headers.get("x-loadouts-deleted") ?? 0)
      sum.renamed += Number(r.headers.get("x-loadouts-renamed") ?? 0)
      sum.overwritten += Number(r.headers.get("x-loadouts-overwritten") ?? 0)
      sum.vesselsReset =
        sum.vesselsReset || r.headers.get("x-vessels-reset") === "1"
      sum.presetsReset =
        sum.presetsReset || r.headers.get("x-presets-reset") === "1"
    }
  }

  sum.filename = filename
  triggerDownload(current, filename)
  return sum
}
