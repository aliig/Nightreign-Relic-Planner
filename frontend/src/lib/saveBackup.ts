import { useEffect, useState } from "react"

/**
 * Durable, client-side backup of the user's *original* imported save.
 *
 * Stored in IndexedDB (binary-capable, survives reloads/restarts) and never
 * sent to a server — a private "oops, I ruined my save" recovery net. A single
 * slot holds the most recently imported file. All operations degrade silently
 * if IndexedDB is unavailable (private mode, disabled storage).
 */
const DB_NAME = "nrp-save-backup"
const STORE = "original"
const KEY = "current"
const CHANGED_EVENT = "nrp-backup-changed"

export type BackupMeta = { name: string; size: number; importedAt: string }
type BackupRecord = BackupMeta & { file: File }

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1)
    req.onupgradeneeded = () => req.result.createObjectStore(STORE)
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

async function readBackup(): Promise<BackupRecord | null> {
  const db = await openDb()
  try {
    return await new Promise<BackupRecord | null>((resolve, reject) => {
      const t = db.transaction(STORE, "readonly")
      const req = t.objectStore(STORE).get(KEY)
      req.onsuccess = () => resolve((req.result as BackupRecord) ?? null)
      req.onerror = () => reject(req.error)
    })
  } finally {
    db.close()
  }
}

/** Stash (or overwrite) the pristine original file. Called at import time. */
export async function storeOriginalBackup(file: File): Promise<void> {
  try {
    const db = await openDb()
    const rec: BackupRecord = {
      file,
      name: file.name,
      size: file.size,
      importedAt: new Date().toISOString(),
    }
    await new Promise<void>((resolve, reject) => {
      const t = db.transaction(STORE, "readwrite")
      t.objectStore(STORE).put(rec, KEY)
      t.oncomplete = () => resolve()
      t.onerror = () => reject(t.error)
    })
    db.close()
    window.dispatchEvent(new Event(CHANGED_EVENT))
  } catch {
    /* IndexedDB unavailable — backup silently skipped. */
  }
}

export async function getOriginalBackupMeta(): Promise<BackupMeta | null> {
  try {
    const rec = await readBackup()
    return rec
      ? { name: rec.name, size: rec.size, importedAt: rec.importedAt }
      : null
  } catch {
    return null
  }
}

/** Re-download the pristine original (keeps its filename, so it's restore-ready). */
export async function downloadOriginalBackup(): Promise<boolean> {
  try {
    const rec = await readBackup()
    if (!rec) return false
    const url = URL.createObjectURL(rec.file)
    const a = document.createElement("a")
    a.href = url
    a.download = rec.name
    a.click()
    URL.revokeObjectURL(url)
    return true
  } catch {
    return false
  }
}

export async function clearOriginalBackup(): Promise<void> {
  try {
    const db = await openDb()
    await new Promise<void>((resolve, reject) => {
      const t = db.transaction(STORE, "readwrite")
      t.objectStore(STORE).delete(KEY)
      t.oncomplete = () => resolve()
      t.onerror = () => reject(t.error)
    })
    db.close()
    window.dispatchEvent(new Event(CHANGED_EVENT))
  } catch {
    /* ignore */
  }
}

/** Reactive view of the current backup's metadata (null if none / unavailable). */
export function useOriginalBackup(): {
  meta: BackupMeta | null
  refresh: () => void
} {
  const [meta, setMeta] = useState<BackupMeta | null>(null)
  useEffect(() => {
    let active = true
    const load = () => {
      getOriginalBackupMeta().then((m) => {
        if (active) setMeta(m)
      })
    }
    load()
    window.addEventListener(CHANGED_EVENT, load)
    return () => {
      active = false
      window.removeEventListener(CHANGED_EVENT, load)
    }
  }, [])
  return {
    meta,
    refresh: () => {
      getOriginalBackupMeta().then(setMeta)
    },
  }
}
