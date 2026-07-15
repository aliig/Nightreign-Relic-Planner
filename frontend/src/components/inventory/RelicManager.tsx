import { Link } from "@tanstack/react-router"
import {
  ArrowUpDown,
  ChevronDown,
  Coins,
  Eye,
  EyeOff,
  Lock,
  Package,
  RotateCcw,
  Star,
  Trash2,
} from "lucide-react"
import { useCallback, useMemo, useState } from "react"

import { EffectList, RelicNameCell } from "@/components/RelicDisplay"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { RelicBuildRef } from "@/hooks/useRelicUsage"
import { setFavorite, toggleSell, usePendingSlot } from "@/lib/pendingChanges"
import { type RelicStatus, relicStatus } from "@/lib/relicStatus"
import { effectCountOf, formatMurks, sellValue } from "@/lib/sellValue"
import { cn } from "@/lib/utils"
import { ActiveFilterChips, InventoryFilters } from "./InventoryFilters"
import {
  applyFilters,
  EMPTY_FILTER,
  type FilterState,
  matchesState,
} from "./relicFilter"
import { isUniqueRelic, type ManagedRelic, RELIC_CAP } from "./types"

type UsageSort =
  | "name"
  | "most"
  | "least"
  | "value-high"
  | "value-low"
  | "newest"
  | "oldest"

const SORT_LABELS: Record<UsageSort, string> = {
  name: "Name (A–Z)",
  most: "Most used",
  least: "Least used",
  "value-high": "Highest value",
  "value-low": "Lowest value",
  newest: "Newest acquired",
  oldest: "Oldest acquired",
}

const STATUS_META: Record<
  RelicStatus,
  { label: string; cls: string; hint: string }
> = {
  active: {
    label: "Active",
    cls: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
    hint: "Equipped in-game and used by one of your builds — keep it.",
  },
  stale: {
    label: "Stale",
    cls: "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400",
    hint: "Equipped in-game, but no current build uses it. A candidate to unequip or replace in-game.",
  },
  bench: {
    label: "On the bench",
    cls: "border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-400",
    hint: "A build wants this relic, but it isn't equipped in-game yet.",
  },
  unused: {
    label: "Unused",
    cls: "border-border bg-muted text-muted-foreground",
    hint: "Not equipped and not used by any build — a candidate to sell.",
  },
}

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
  usage: Map<number, RelicBuildRef[]>
  slotIndex: number
  murks: number
}) {
  const pending = usePendingSlot(slotIndex)
  // The trashed set is the live "this relic is gone" state — sold relics drop out
  // of the inventory entirely (unless "Show trashed" is on).
  const trashed = useMemo(() => new Set(pending.sells), [pending.sells])
  const favoriteChanges = pending.favorites

  const [filter, setFilter] = useState<FilterState>(EMPTY_FILTER)
  const [usageSort, setUsageSort] = useState<UsageSort>("name")
  const [showTrashed, setShowTrashed] = useState(false)
  // Transient multi-select for the "Trash selected" bulk action — NOT persisted.
  const [selection, setSelection] = useState<Set<number>>(new Set())

  // Memoized so isSellable is stable across renders (the visible-list memo and
  // the bulk-select depend on it); rebinds only when pending favorites change.
  const effectiveFavorite = useCallback(
    (r: ManagedRelic): boolean =>
      r.gaHandle in favoriteChanges
        ? favoriteChanges[r.gaHandle]
        : r.isFavorite,
    [favoriteChanges],
  )

  // Unique relics are one-of-a-kind and can't be re-acquired, so they're locked
  // from trashing just like equipped relics — guard against accidental deletion.
  const isSellable = useCallback(
    (r: ManagedRelic): boolean =>
      !r.equipped && !effectiveFavorite(r) && !isUniqueRelic(r.realId),
    [effectiveFavorite],
  )

  const buildsOf = (r: ManagedRelic): RelicBuildRef[] =>
    usage.get(r.realId) ?? []
  const usageOf = (r: ManagedRelic): number => buildsOf(r).length

  const metaFor = (r: ManagedRelic) => ({
    name: r.name,
    isDeep: r.isDeep,
    murk: sellValue(effectCountOf(r.effects), r.isDeep),
    builds: usageOf(r),
  })

  const visible = useMemo(() => {
    const u = (r: ManagedRelic) => usage.get(r.realId)?.length ?? 0
    const relicValue = (r: ManagedRelic) =>
      sellValue(effectCountOf(r.effects), r.isDeep)
    let list = applyFilters(relics, filter, effectMap)
    // The State axes (sellable/equipped/in-a-build/bookmarked) depend on live
    // usage + pending favorites, which only exist here — so they're filtered
    // here rather than in applyFilters, which only sees relic-intrinsic fields.
    list = list.filter((r) =>
      matchesState(filter, {
        equipped: r.equipped,
        used: u(r) > 0,
        favorite: effectiveFavorite(r),
        sellable: isSellable(r),
      }),
    )
    // Trashed relics are hidden by default — they've left the inventory.
    if (!showTrashed) {
      list = list.filter((r) => !trashed.has(r.gaHandle))
    }
    const sorted = [...list]
    if (usageSort === "most") {
      sorted.sort((a, b) => u(b) - u(a) || a.name.localeCompare(b.name))
    } else if (usageSort === "least") {
      sorted.sort((a, b) => u(a) - u(b) || a.name.localeCompare(b.name))
    } else if (usageSort === "value-high") {
      sorted.sort(
        (a, b) => relicValue(b) - relicValue(a) || a.name.localeCompare(b.name),
      )
    } else if (usageSort === "value-low") {
      sorted.sort(
        (a, b) => relicValue(a) - relicValue(b) || a.name.localeCompare(b.name),
      )
    } else if (usageSort === "newest" || usageSort === "oldest") {
      // acquisitionId is the game's global acquisition counter (higher =
      // acquired later). Null (pre-column uploads) sorts last either way.
      const dir = usageSort === "newest" ? -1 : 1
      sorted.sort((a, b) => {
        if (a.acquisitionId == null && b.acquisitionId == null)
          return a.name.localeCompare(b.name)
        if (a.acquisitionId == null) return 1
        if (b.acquisitionId == null) return -1
        return (
          dir * (a.acquisitionId - b.acquisitionId) ||
          a.name.localeCompare(b.name)
        )
      })
    } else {
      sorted.sort((a, b) => a.name.localeCompare(b.name))
    }
    return sorted
  }, [
    relics,
    filter,
    effectMap,
    usageSort,
    usage,
    showTrashed,
    trashed,
    isSellable,
    effectiveFavorite,
  ])

  // Murk reflects the modified save: every trashed relic's value is already added.
  const trashedRelics = useMemo(
    () => relics.filter((r) => trashed.has(r.gaHandle)),
    [relics, trashed],
  )
  const murkGain = trashedRelics.reduce(
    (sum, r) => sum + sellValue(effectCountOf(r.effects), r.isDeep),
    0,
  )
  const projectedMurks = Math.min(murks + murkGain, 0xffffffff)
  const trashedCount = trashedRelics.length
  // Owned-vs-cap readout reflects pending trashes — each trashed relic frees one
  // slot toward the in-game storage cap.
  const projectedRelicCount = relics.length - trashedCount

  function trash(r: ManagedRelic) {
    if (!isSellable(r) || trashed.has(r.gaHandle)) return
    toggleSell(slotIndex, r.gaHandle, metaFor(r))
    // Drop it from the transient bulk selection so the count stays honest.
    setSelection((prev) => {
      if (!prev.has(r.gaHandle)) return prev
      const next = new Set(prev)
      next.delete(r.gaHandle)
      return next
    })
  }

  function restore(r: ManagedRelic) {
    if (trashed.has(r.gaHandle)) toggleSell(slotIndex, r.gaHandle)
  }

  function toggleFavorite(r: ManagedRelic) {
    const desired = !effectiveFavorite(r)
    // null clears the change when it matches the saved state.
    setFavorite(
      slotIndex,
      r.gaHandle,
      desired === r.isFavorite ? null : desired,
      { name: r.name, isDeep: r.isDeep },
    )
    // Bookmarking a relic makes it unsellable — pull it back out of the trash.
    if (desired && trashed.has(r.gaHandle)) {
      toggleSell(slotIndex, r.gaHandle)
    }
  }

  // --- transient selection (bulk trash) -------------------------------------

  const selectableVisible = visible.filter(
    (r) => isSellable(r) && !trashed.has(r.gaHandle),
  )
  const allSelected =
    selectableVisible.length > 0 &&
    selectableVisible.every((r) => selection.has(r.gaHandle))

  function toggleSelect(r: ManagedRelic) {
    setSelection((prev) => {
      const next = new Set(prev)
      if (next.has(r.gaHandle)) next.delete(r.gaHandle)
      else next.add(r.gaHandle)
      return next
    })
  }

  function toggleSelectAll() {
    setSelection((prev) => {
      if (allSelected) {
        const next = new Set(prev)
        for (const r of selectableVisible) next.delete(r.gaHandle)
        return next
      }
      const next = new Set(prev)
      for (const r of selectableVisible) next.add(r.gaHandle)
      return next
    })
  }

  function trashSelected() {
    for (const r of relics) {
      if (
        selection.has(r.gaHandle) &&
        isSellable(r) &&
        !trashed.has(r.gaHandle)
      )
        toggleSell(slotIndex, r.gaHandle, metaFor(r))
    }
    setSelection(new Set())
  }

  const selectedCount = selection.size

  return (
    <div className="space-y-4">
      {/* Filters + sort + show-trashed + murk readout, then active-filter chips */}
      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <InventoryFilters
              filter={filter}
              setFilter={setFilter}
              effectsData={effectsData}
            />
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm" className="h-9 gap-1.5">
                  <ArrowUpDown className="h-3.5 w-3.5 opacity-70" />
                  {SORT_LABELS[usageSort]}
                  <ChevronDown className="h-3.5 w-3.5 opacity-50" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                <DropdownMenuRadioGroup
                  value={usageSort}
                  onValueChange={(v) => setUsageSort(v as UsageSort)}
                >
                  <DropdownMenuRadioItem value="name">
                    Name (A–Z)
                  </DropdownMenuRadioItem>
                  <DropdownMenuRadioItem value="most">
                    Most used
                  </DropdownMenuRadioItem>
                  <DropdownMenuRadioItem value="least">
                    Least used
                  </DropdownMenuRadioItem>
                  <DropdownMenuRadioItem value="value-high">
                    Highest value
                  </DropdownMenuRadioItem>
                  <DropdownMenuRadioItem value="value-low">
                    Lowest value
                  </DropdownMenuRadioItem>
                  <DropdownMenuRadioItem value="newest">
                    Newest acquired
                  </DropdownMenuRadioItem>
                  <DropdownMenuRadioItem value="oldest">
                    Oldest acquired
                  </DropdownMenuRadioItem>
                </DropdownMenuRadioGroup>
              </DropdownMenuContent>
            </DropdownMenu>
            {trashedCount > 0 && (
              <Button
                variant={showTrashed ? "secondary" : "outline"}
                size="sm"
                className="h-9"
                onClick={() => setShowTrashed((v) => !v)}
              >
                {showTrashed ? (
                  <EyeOff className="mr-1.5 h-3.5 w-3.5" />
                ) : (
                  <Eye className="mr-1.5 h-3.5 w-3.5" />
                )}
                {showTrashed ? "Hide" : "Show"} trashed ({trashedCount})
              </Button>
            )}
          </div>
          <div className="flex items-center gap-4 text-sm">
            <div className="flex items-center gap-2">
              <Coins className="h-4 w-4 text-amber-500" />
              <span className="font-medium">{formatMurks(projectedMurks)}</span>
              {murkGain > 0 && (
                <span className="text-green-600 dark:text-green-500">
                  (+{formatMurks(murkGain)} from {trashedCount} trashed)
                </span>
              )}
            </div>
            <div
              className="flex items-center gap-2"
              title="In-game relic storage cap. Trash relics to free space — this count updates as you do."
            >
              <Package className="h-4 w-4 text-muted-foreground" />
              <span className="font-medium">
                {projectedRelicCount.toLocaleString()} /{" "}
                {RELIC_CAP.toLocaleString()}
              </span>
            </div>
          </div>
        </div>
        <ActiveFilterChips
          filter={filter}
          setFilter={setFilter}
          effectMap={effectMap}
        />
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
              <TableHead className="w-32">Status</TableHead>
              <TableHead>Effects</TableHead>
              <TableHead className="w-12 text-center">Mark</TableHead>
              <TableHead className="w-12 text-center">Trash</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {visible.map((relic) => {
              const fav = effectiveFavorite(relic)
              const sellable = isSellable(relic)
              const isTrashed = trashed.has(relic.gaHandle)
              const builds = buildsOf(relic)
              const count = builds.length
              const status = relicStatus(relic.equipped, count > 0)
              const isUnique = isUniqueRelic(relic.realId)
              const lockReason = relic.equipped
                ? "Equipped — can't trash"
                : isUnique
                  ? "Unique relic — can't be re-acquired, so it's locked"
                  : fav
                    ? "Bookmarked — un-bookmark to trash"
                    : undefined
              // Effects and curses share one column now: curses stack beneath the
              // effects (rendered red), so the action icons stay in view.
              const effectsCell = EffectList({
                effectIds: relic.effects,
                isCurse: false,
                effectMap,
              })
              const cursesCell = EffectList({
                effectIds: relic.curses,
                isCurse: true,
                effectMap,
              })
              return (
                <TableRow
                  key={relic.key}
                  className={isTrashed ? "opacity-50" : undefined}
                  data-state={
                    selection.has(relic.gaHandle) ? "selected" : undefined
                  }
                >
                  <TableCell>
                    {sellable ? (
                      <Checkbox
                        checked={selection.has(relic.gaHandle)}
                        disabled={isTrashed}
                        onCheckedChange={() => toggleSelect(relic)}
                        aria-label={`Select ${relic.name}`}
                      />
                    ) : (
                      <span
                        role="img"
                        className="inline-flex p-0.5 text-muted-foreground"
                        title={lockReason}
                        aria-label={lockReason}
                      >
                        <Lock className="h-3.5 w-3.5" />
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="min-w-[180px]">
                    <span className={isTrashed ? "line-through" : ""}>
                      <RelicNameCell
                        name={relic.name}
                        color={relic.color}
                        tier={relic.tier}
                        isDeep={relic.isDeep}
                      />
                    </span>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-col items-start gap-1">
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Badge
                            variant="outline"
                            tabIndex={0}
                            className={cn(
                              "cursor-help font-normal focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                              STATUS_META[status].cls,
                            )}
                          >
                            {STATUS_META[status].label}
                          </Badge>
                        </TooltipTrigger>
                        <TooltipContent side="right" className="max-w-[15rem]">
                          {STATUS_META[status].hint}
                        </TooltipContent>
                      </Tooltip>
                      {count > 0 && (
                        <Popover>
                          <PopoverTrigger asChild>
                            <button
                              type="button"
                              className="text-xs text-muted-foreground underline decoration-dotted underline-offset-2 hover:text-foreground"
                            >
                              {count} build{count !== 1 ? "s" : ""}
                            </button>
                          </PopoverTrigger>
                          <PopoverContent side="right" className="w-56 p-2">
                            <p className="mb-1.5 px-1.5 text-xs font-medium text-muted-foreground">
                              Used by {count} build{count !== 1 ? "s" : ""} —
                              open in the Optimizer:
                            </p>
                            <ul className="space-y-0.5">
                              {builds.map((b) => (
                                <li key={b.id}>
                                  <Link
                                    to="/builds/$buildId/optimize"
                                    params={{ buildId: b.id }}
                                    className="block truncate rounded px-1.5 py-1 text-sm hover:bg-accent hover:text-accent-foreground"
                                  >
                                    {b.name}
                                  </Link>
                                </li>
                              ))}
                            </ul>
                          </PopoverContent>
                        </Popover>
                      )}
                    </div>
                  </TableCell>
                  <TableCell>
                    {effectsCell || cursesCell ? (
                      <div className="flex flex-col gap-1.5">
                        {effectsCell}
                        {cursesCell}
                      </div>
                    ) : (
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
                        }`}
                      />
                    </button>
                  </TableCell>
                  <TableCell className="text-center">
                    {isTrashed ? (
                      <button
                        type="button"
                        onClick={() => restore(relic)}
                        title="Restore relic"
                        aria-label={`Restore ${relic.name}`}
                        className="inline-flex p-1 rounded hover:bg-accent"
                      >
                        <RotateCcw className="h-4 w-4 text-muted-foreground" />
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => trash(relic)}
                        disabled={!sellable}
                        title={
                          lockReason ??
                          (count > 0
                            ? `Used in ${count} build${count !== 1 ? "s" : ""} — trashing may change their best result`
                            : "Trash relic")
                        }
                        aria-label={`Trash ${relic.name}`}
                        className="inline-flex p-1 rounded hover:bg-accent disabled:opacity-30 disabled:cursor-not-allowed"
                      >
                        <Trash2 className="h-4 w-4 text-muted-foreground hover:text-destructive" />
                      </button>
                    )}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}

      {/* Bulk-action bar for the transient selection. */}
      {selectedCount > 0 && (
        <div className="sticky bottom-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card p-3 shadow-lg">
          <span className="text-sm">
            <strong>{selectedCount}</strong> selected
          </span>
          <div className="flex gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setSelection(new Set())}
            >
              Clear selection
            </Button>
            <Button
              variant="destructive"
              size="sm"
              onClick={trashSelected}
              className="gap-1.5"
            >
              <Trash2 className="h-4 w-4" />
              Trash {selectedCount} selected
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
