import { Coins, Lock, Star } from "lucide-react"
import { useMemo, useState } from "react"

import { EffectList, RelicNameCell } from "@/components/RelicDisplay"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
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
import {
  clearSlotRelics,
  setFavorite,
  toggleSell,
  usePendingSlot,
} from "@/lib/pendingChanges"
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
}: {
  relics: ManagedRelic[]
  effectsData: unknown[]
  effectMap: Map<number, string>
  usage: Map<number, number>
  slotIndex: number
  murks: number
}) {
  const pending = usePendingSlot(slotIndex)
  const selected = useMemo(() => new Set(pending.sells), [pending.sells])
  const favoriteChanges = pending.favorites

  const [filter, setFilter] = useState<FilterState>(EMPTY_FILTER)
  const [usageSort, setUsageSort] = useState<UsageSort>("name")

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

  function toggleSelect(r: ManagedRelic) {
    if (!isSellable(r)) return
    toggleSell(slotIndex, r.gaHandle)
  }

  function toggleFavorite(r: ManagedRelic) {
    const desired = !effectiveFavorite(r)
    // null clears the pending change when it matches the saved state.
    setFavorite(
      slotIndex,
      r.gaHandle,
      desired === r.isFavorite ? null : desired,
    )
    // Bookmarking a relic makes it unsellable — drop it from the sell set.
    if (desired && pending.sells.includes(r.gaHandle)) {
      toggleSell(slotIndex, r.gaHandle)
    }
  }

  function toggleSelectAll() {
    const sellableVisible = visible.filter(isSellable)
    const allSelected =
      sellableVisible.length > 0 &&
      sellableVisible.every((r) => selected.has(r.gaHandle))
    for (const r of sellableVisible) {
      const has = selected.has(r.gaHandle)
      if (allSelected ? has : !has) toggleSell(slotIndex, r.gaHandle)
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

      {/* Pending summary — the actual export lives in the top-bar button. */}
      {hasChanges && (
        <div className="sticky bottom-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card p-3 shadow-lg">
          <div className="text-sm">
            {selectedRelics.length > 0 && (
              <span>
                <strong>{selectedRelics.length}</strong> to sell · +
                {formatMurks(murkGain)} Murk (new total{" "}
                {formatMurks(projectedMurks)})
              </span>
            )}
            {selectedRelics.length > 0 && favoriteEdits > 0 && " · "}
            {favoriteEdits > 0 && (
              <span>
                <strong>{favoriteEdits}</strong> bookmark change
                {favoriteEdits !== 1 ? "s" : ""}
              </span>
            )}
            <span className="text-muted-foreground">
              {" "}
              — queued; export from the “Export save” button up top.
            </span>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => clearSlotRelics(slotIndex)}
          >
            Clear
          </Button>
        </div>
      )}
    </div>
  )
}
