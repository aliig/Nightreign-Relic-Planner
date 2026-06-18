/**
 * In-session store for the uploaded save File.
 *
 * The raw save is never persisted server-side, and a File/binary can't live in
 * sessionStorage. So we hold the File in a module variable (survives SPA
 * navigation, lost on a full page reload) plus a small sessionStorage marker so
 * the UI knows a save *was* loaded and can prompt the user to re-select it after
 * a reload.
 */
const STORAGE_KEY = "saveFileMeta"

let currentFile: File | null = null

export type SaveFileMeta = {
  name: string
  size: number
}

export function rememberSaveFile(file: File): void {
  currentFile = file
  const meta: SaveFileMeta = { name: file.name, size: file.size }
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(meta))
}

export function getSaveFile(): File | null {
  return currentFile
}

export function getSaveFileMeta(): SaveFileMeta | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as SaveFileMeta) : null
  } catch {
    return null
  }
}

export function clearSaveFile(): void {
  currentFile = null
  sessionStorage.removeItem(STORAGE_KEY)
}
