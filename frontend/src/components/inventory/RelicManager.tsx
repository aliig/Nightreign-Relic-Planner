import { AlertTriangle, Coins, Download, Lock, Star } from "lucide-react"
import { useMemo, useRef, useState } from "react"

import { EffectList, RelicNameCell } from "@/components/RelicDisplay"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import useCustomToast from "@/hooks/useCustomToast"
import { ExportError, exportModifiedSave } from "@/lib/exportSave"
import { getSaveFile, getSaveFileMeta, rememberSaveFile } from "@/lib/saveFile"
import { effectCountOf, formatMurks, sellValue } from "@/lib/sellValue"
import {
  applyFilters,
  EMPTY_FILTER,
  type FilterState,
  InventoryFilters,
} from "./InventoryFilters"
import type { ManagedRelic } from "./types"

type UsageSort = "name" | "most" | "least" | "unused"

export function RelicManager({
  relics,
  effectsData,
  effectMap,
  usage,
  slotIndex,
  murks,
  onExported,
}: {
  relics: ManagedRelic[]
  effectsData: unknown[]
  effectMap: Map<number, string>
  usage: Map<number, number>
  slotIndex: number
  murks: number
  /** Called after a successful export so the caller can reset/refresh. */
  onExported?: () => void
}) {
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const [filter, setFilter] = useState<FilterState>(EMPTY_FILTER)
  const [usageSort, setUsageSort] = useState<UsageSort>("name")
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [favoriteChanges, setFavoriteChanges] = useState<
    Record<number, boolean>
  >({})
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  // Bumped when the user (re-)selects a save file so we re-read getSaveFile().
  const [, setFileTick] = useState(0)

  const effectiveFavorite = (r: ManagedRelic): boolean =>
    r.gaHandle in favoriteChanges ? favoriteChanges[r.gaHandle] : r.isFavorite

  const isSellable = (r: ManagedRelic): boolean =>
    !r.equipped && !effectiveFavorite(r)

  const usageOf = (r: ManagedRelic): number => usage.get(r.realId) ?? 0

  const visible = useMemo(() => {
    const u = (r: ManagedRelic) => usage.get(r.realId) ?? 0
    let list = applyFilters(relics, filter, effectMap)
    if (usageSort === "unused") {
      list = list.filter((r) => u(r) === 0)
    }
    const sorted = [...list]
    if (usageSort === "most") {
      sorted.sort((a, b) => u(b) - u(a) || a.name.localeCompare(b.name))
    } else if (usageSort === "least") {
      sorted.sort((a, b) => u(a) - u(b) || a.name.localeCompare(b.name))
    } else {
      sorted.sort((a, b) => a.name.localeCompare(b.name))
    }
    return sorted
  }, [relics, filter, effectMap, usageSort, usage])

  const selectedRelics = useMemo(
    () =>
      relics.filter((r) => {
        if (!selected.has(r.gaHandle) || r.equipped) return false
        const fav =
          r.gaHandle in favoriteChanges
            ? favoriteChanges[r.gaHandle]
            : r.isFavorite
        return !fav
      }),
    [relics, selected, favoriteChanges],
  )

  const murkGain = selectedRelics.reduce(
    (sum, r) => sum + sellValue(effectCountOf(r.effects), r.isDeep),
    0,
  )
  const projectedMurks = Math.min(murks + murkGain, 0xffffffff)

  const favoriteEdits = Object.keys(favoriteChanges).length
  const hasChanges = selectedRelics.length > 0 || favoriteEdits > 0

  const saveFile = getSaveFile()
  const saveMeta = getSaveFileMeta()

  function toggleSelect(r: ManagedRelic) {
    if (!isSellable(r)) return
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(r.gaHandle)) next.delete(r.gaHandle)
      else next.add(r.gaHandle)
      return next
    })
  }

  function toggleFavorite(r: ManagedRelic) {
    const desired = !effectiveFavorite(r)
    setFavoriteChanges((prev) => {
      const next = { ...prev }
      if (desired === r.isFavorite) delete next[r.gaHandle]
      else next[r.gaHandle] = desired
      return next
    })
    // Bookmarking a relic makes it unsellable — drop it from the sell set.
    if (desired) {
      setSelected((prev) => {
        if (!prev.has(r.gaHandle)) return prev
        const next = new Set(prev)
        next.delete(r.gaHandle)
        return next
      })
    }
  }

  function toggleSelectAll() {
    const sellableVisible = visible.filter(isSellable)
    const allSelected =
      sellableVisible.length > 0 &&
      sellableVisible.every((r) => selected.has(r.gaHandle))
    setSelected((prev) => {
      const next = new Set(prev)
      if (allSelected) {
        for (const r of sellableVisible) next.delete(r.gaHandle)
      } else {
        for (const r of sellableVisible) next.add(r.gaHandle)
      }
      return next
    })
  }

  function onPickFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (file) {
      rememberSaveFile(file)
      setFileTick((t) => t + 1)
    }
  }

  async function doExport() {
    const file = getSaveFile()
    if (!file) return
    setBusy(true)
    try {
      const result = await exportModifiedSave({
        file,
        slotIndex,
        gaHandles: selectedRelics.map((r) => r.gaHandle),
        favoriteChanges,
      })
      setConfirmOpen(false)
      setSelected(new Set())
      setFavoriteChanges({})
      const parts: string[] = []
      if (result.removed > 0)
        parts.push(
          `sold ${result.removed} relic${result.removed !== 1 ? "s" : ""} (+${formatMurks(result.murkCredit)} Murk)`,
        )
      if (result.favoritesChanged > 0)
        parts.push(
          `${result.favoritesChanged} bookmark change${result.favoritesChanged !== 1 ? "s" : ""}`,
        )
      showSuccessToast(
        `Saved ${result.filename} — ${parts.join(", ")}. Load it in-game, then re-import here to refresh.`,
      )
      onExported?.()
    } catch (err) {
      showErrorToast(
        err instanceof ExportError || err instanceof Error
          ? err.message
          : "Export failed",
      )
    } finally {
      setBusy(false)
    }
  }

  const sellableVisible = visible.filter(isSellable)
  const allSelected =
    sellableVisible.length > 0 &&
    sellableVisible.every((r) => selected.has(r.gaHandle))

  return (
    <div className="space-y-4">
      {/* Murk + filters */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-3">
          <InventoryFilters
            filter={filter}
            setFilter={setFilter}
            effectsData={effectsData}
          />
          <Select
            value={usageSort}
            onValueChange={(v) => setUsageSort(v as UsageSort)}
          >
            <SelectTrigger className="w-40">
              <SelectValue placeholder="Sort by usage" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="name">Name (A–Z)</SelectItem>
              <SelectItem value="least">Least used</SelectItem>
              <SelectItem value="most">Most used</SelectItem>
              <SelectItem value="unused">Unused only</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <Coins className="h-4 w-4 text-amber-500" />
          <span className="font-medium">{formatMurks(murks)}</span>
          {murkGain > 0 && (
            <span className="text-green-600 dark:text-green-500">
              → {formatMurks(projectedMurks)} (+{formatMurks(murkGain)})
            </span>
          )}
        </div>
      </div>

      {visible.length === 0 ? (
        <p className="text-sm text-muted-foreground py-8 text-center">
          No relics match the current filters.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-10">
                <Checkbox
                  checked={allSelected}
                  onCheckedChange={toggleSelectAll}
                  aria-label="Select all sellable relics"
                />
              </TableHead>
              <TableHead>Relic</TableHead>
              <TableHead className="w-24">Setups</TableHead>
              <TableHead>Effects</TableHead>
              <TableHead>Curses</TableHead>
              <TableHead className="w-12 text-center">Mark</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {visible.map((relic) => {
              const fav = effectiveFavorite(relic)
              const sellable = isSellable(relic)
              const count = usageOf(relic)
              const lockReason = relic.equipped
                ? "Equipped — can't sell"
                : fav
                  ? "Bookmarked — un-bookmark to sell"
                  : undefined
              return (
                <TableRow
                  key={relic.key}
                  data-state={
                    selected.has(relic.gaHandle) ? "selected" : undefined
                  }
                >
                  <TableCell>
                    <Checkbox
                      checked={selected.has(relic.gaHandle)}
                      disabled={!sellable}
                      onCheckedChange={() => toggleSelect(relic)}
                      aria-label={`Select ${relic.name}`}
                      title={lockReason}
                    />
                  </TableCell>
                  <TableCell className="min-w-[180px]">
                    <div className="flex items-center gap-1.5">
                      <RelicNameCell
                        name={relic.name}
                        color={relic.color}
                        tier={relic.tier}
                        isDeep={relic.isDeep}
                      />
                      {relic.equipped && (
                        <Lock
                          className="h-3 w-3 text-muted-foreground shrink-0"
                          aria-label="Equipped"
                        />
                      )}
                    </div>
                  </TableCell>
                  <TableCell>
                    {count > 0 ? (
                      <Badge variant="secondary" className="font-normal">
                        {count} build{count !== 1 ? "s" : ""}
                      </Badge>
                    ) : (
                      <span className="text-xs text-muted-foreground">
                        Unused
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    {EffectList({
                      effectIds: relic.effects,
                      isCurse: false,
                      effectMap,
                    }) ?? (
                      <span className="text-xs text-muted-foreground italic">
                        —
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    {EffectList({
                      effectIds: relic.curses,
                      isCurse: true,
                      effectMap,
                    }) ?? (
                      <span className="text-xs text-muted-foreground italic">
                        —
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-center">
                    <button
                      type="button"
                      onClick={() => toggleFavorite(relic)}
                      title={fav ? "Remove bookmark" : "Bookmark"}
                      aria-label={fav ? "Remove bookmark" : "Bookmark"}
                      className="inline-flex p-1 rounded hover:bg-accent"
                    >
                      <Star
                        className={`h-4 w-4 ${
                          fav
                            ? "fill-amber-400 text-amber-500"
                            : "text-muted-foreground"
                        } ${relic.gaHandle in favoriteChanges ? "ring-1 ring-amber-400 rounded" : ""}`}
                      />
                    </button>
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}

      {/* Action bar */}
      {hasChanges && (
        <div className="sticky bottom-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card p-3 shadow-lg">
          <div className="text-sm">
            {selectedRelics.length > 0 && (
              <span>
                <strong>{selectedRelics.length}</strong> to sell · +
                {formatMurks(murkGain)} Murk
              </span>
            )}
            {selectedRelics.length > 0 && favoriteEdits > 0 && " · "}
            {favoriteEdits > 0 && (
              <span>
                <strong>{favoriteEdits}</strong> bookmark change
                {favoriteEdits !== 1 ? "s" : ""}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setSelected(new Set())
                setFavoriteChanges({})
              }}
            >
              Clear
            </Button>
            <Button size="sm" onClick={() => setConfirmOpen(true)}>
              <Download className="mr-1.5 h-4 w-4" />
              Export modified save
            </Button>
          </div>
        </div>
      )}

      {/* Confirmation dialog */}
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Export modified save</DialogTitle>
            <DialogDescription asChild>
              <div className="space-y-2 pt-1">
                <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-amber-700 dark:text-amber-400">
                  <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
                  <span>
                    Back up your original save first. This downloads a modified
                    copy — close the game, then replace your save with it.
                  </span>
                </div>
                <ul className="text-sm list-disc pl-5">
                  {selectedRelics.length > 0 && (
                    <li>
                      Sell {selectedRelics.length} relic
                      {selectedRelics.length !== 1 ? "s" : ""} for{" "}
                      {formatMurks(murkGain)} Murk (new total{" "}
                      {formatMurks(projectedMurks)}).
                    </li>
                  )}
                  {favoriteEdits > 0 && (
                    <li>
                      Apply {favoriteEdits} bookmark change
                      {favoriteEdits !== 1 ? "s" : ""}.
                    </li>
                  )}
                </ul>
              </div>
            </DialogDescription>
          </DialogHeader>

          {!saveFile && (
            <div className="space-y-1 text-sm">
              <p className="text-muted-foreground">
                {saveMeta
                  ? `Re-select your save file (${saveMeta.name}) to export — it must be the same save the inventory was loaded from.`
                  : "Select your save file (.sl2) to export."}
              </p>
              <input
                ref={fileInputRef}
                type="file"
                accept=".sl2"
                onChange={onPickFile}
                className="text-sm"
              />
            </div>
          )}

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setConfirmOpen(false)}
              disabled={busy}
            >
              Cancel
            </Button>
            <Button onClick={doExport} disabled={busy || !saveFile}>
              {busy ? "Exporting…" : "Download modified save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
