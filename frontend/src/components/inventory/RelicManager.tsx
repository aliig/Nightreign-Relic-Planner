import {
  ArrowUpDown,
  ChevronDown,
  Coins,
  Eye,
  EyeOff,
  Package,
  Trash2,
} from "lucide-react"
import { useCallback, useMemo, useState } from "react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import type { BuildUsageInfo, RelicUsage } from "@/hooks/useRelicUsage"
import {
  addSells,
  effectiveMurks,
  setFavorite,
  toggleSell,
  usePendingSlot,
} from "@/lib/pendingChanges"
import { effectCountOf, formatMurks, sellValue } from "@/lib/sellValue"
import { ActiveFilterChips, InventoryFilters } from "./InventoryFilters"
import { RelicTable } from "./RelicTable"
import {
  applyFilters,
  EMPTY_FILTER,
  type FilterState,
  matchesState,
} from "./relicFilter"
import { TIER_META, TIER_ORDER, tierRank } from "./tiers"
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
  most: "Keepers first",
  least: "Dead weight first",
  "value-high": "Highest value",
  "value-low": "Lowest value",
  newest: "Newest acquired",
  oldest: "Oldest acquired",
}

export function RelicManager({
  relics,
  effectsData,
  effectMap,
  usage,
  buildsById,
  usageKnown,
  usageUnavailable,
  slotIndex,
  murks,
}: {
  relics: ManagedRelic[]
  effectsData: unknown[]
  effectMap: Map<number, string>
  // Keyed by ga_handle — per PHYSICAL relic.  Keyed by real_id (relic TYPE),
  // one placed copy marked every content-identical copy as used.
  usage: Map<number, RelicUsage>
  buildsById: Map<string, BuildUsageInfo>
  // False until the first usage response lands.  Rendering a verdict while the
  // answer is still in flight is the lie this page kept telling.
  usageKnown: boolean
  // The usage request FAILED (as opposed to not having landed yet).
  usageUnavailable: boolean
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
  const [confirmOpen, setConfirmOpen] = useState(false)
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
  // Incoming (staged-mint) rows aren't in the save yet: they can only be undone
  // from the Changes panel, never trashed/bookmarked here.
  const isSellable = useCallback(
    (r: ManagedRelic): boolean =>
      !r.incoming &&
      !r.equipped &&
      !effectiveFavorite(r) &&
      !isUniqueRelic(r.realId),
    [effectiveFavorite],
  )

  const usageOf = useCallback(
    (r: ManagedRelic): number => usage.get(r.gaHandle)?.used_by?.length ?? 0,
    [usage],
  )

  const metaFor = useCallback(
    (r: ManagedRelic) => ({
      name: r.name,
      isDeep: r.isDeep,
      murk: sellValue(effectCountOf(r.effects), r.isDeep),
      builds: usageOf(r),
      // Content fingerprint — the only cross-save identity (handles renumber);
      // the upload divergence gate uses it to detect applied sells.
      fp: [r.realId, ...r.effects, ...r.curses],
    }),
    [usageOf],
  )

  const visible = useMemo(() => {
    const relicValue = (r: ManagedRelic) =>
      sellValue(effectCountOf(r.effects), r.isDeep)
    let list = applyFilters(relics, filter, effectMap)
    // The State axes (sellable/equipped/in-a-build/bookmarked) depend on live
    // usage + pending favorites, which only exist here — so they're filtered
    // here rather than in applyFilters, which only sees relic-intrinsic fields.
    // Until the usage answer lands, the tier axis is DROPPED rather than
    // passed unknown tiers: matchesState lets a null tier through (a row must
    // not vanish mid-flight), which as a filter would list the entire
    // inventory under "Dead weight" and invite the user to select all and
    // trash it.  No answer means the question cannot be asked yet.
    const stateFilter = usageKnown ? filter : { ...filter, usageTiers: [] }
    list = list.filter((r) =>
      matchesState(stateFilter, {
        equipped: r.equipped,
        tier: usage.get(r.gaHandle)?.tier ?? null,
        favorite: effectiveFavorite(r),
        sellable: isSellable(r),
      }),
    )
    // Trashed relics are hidden by default — they've left the inventory.
    if (!showTrashed) {
      list = list.filter((r) => !trashed.has(r.gaHandle))
    }
    const sorted = [...list]
    // Tier order (in_use -> dead) is the culling signal, so "most/least used"
    // now ranks by tier rather than by a raw build count.  An unknown tier
    // (usage still loading) sorts last either way.
    const rank = (r: ManagedRelic) => {
      const t = usage.get(r.gaHandle)?.tier
      return t ? tierRank(t) : Number.POSITIVE_INFINITY
    }
    if (usageSort === "most") {
      sorted.sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name))
    } else if (usageSort === "least") {
      sorted.sort((a, b) => rank(b) - rank(a) || a.name.localeCompare(b.name))
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
    usageKnown,
    showTrashed,
    trashed,
    isSellable,
    effectiveFavorite,
  ])

  // Murk reflects the modified save: every trashed relic's value is already
  // added, and the staged Relic Rites batch's net delta (murkDelta, usually a
  // cost) is applied too — the two are disjoint by construction (rites sells
  // only what it buys; trashes here are pre-owned relics). The total comes
  // from the shared live-Murk selector so every page projects the same value;
  // murkGain is recomputed from live relic data only for the breakdown chip.
  const trashedRelics = useMemo(
    () => relics.filter((r) => trashed.has(r.gaHandle)),
    [relics, trashed],
  )
  const murkGain = trashedRelics.reduce(
    (sum, r) => sum + sellValue(effectCountOf(r.effects), r.isDeep),
    0,
  )
  const projectedMurks = effectiveMurks(murks, pending) ?? 0
  const trashedCount = trashedRelics.length
  // Owned-vs-cap readout reflects pending trashes and staged mints — `relics`
  // already contains the incoming rows, each occupying one storage slot.
  const projectedRelicCount = relics.length - trashedCount

  // Every handler below is useCallback-stable: RelicRow is memo'd, so an
  // identity that changed each render would re-render every mounted row.
  const trash = useCallback(
    (r: ManagedRelic) => {
      if (!isSellable(r) || trashed.has(r.gaHandle)) return
      toggleSell(slotIndex, r.gaHandle, metaFor(r))
      // Drop it from the transient bulk selection so the count stays honest.
      setSelection((prev) => {
        if (!prev.has(r.gaHandle)) return prev
        const next = new Set(prev)
        next.delete(r.gaHandle)
        return next
      })
    },
    [isSellable, trashed, slotIndex, metaFor],
  )

  const restore = useCallback(
    (r: ManagedRelic) => {
      if (trashed.has(r.gaHandle)) toggleSell(slotIndex, r.gaHandle)
    },
    [trashed, slotIndex],
  )

  const toggleFavorite = useCallback(
    (r: ManagedRelic) => {
      if (r.incoming) return
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
    },
    [effectiveFavorite, trashed, slotIndex],
  )

  // --- transient selection (bulk trash) -------------------------------------

  const selectableVisible = useMemo(
    () => visible.filter((r) => isSellable(r) && !trashed.has(r.gaHandle)),
    [visible, isSellable, trashed],
  )
  const selectedVisibleCount = useMemo(
    () => selectableVisible.filter((r) => selection.has(r.gaHandle)).length,
    [selectableVisible, selection],
  )
  const allSelected =
    selectableVisible.length > 0 &&
    selectedVisibleCount === selectableVisible.length

  const toggleSelect = useCallback((r: ManagedRelic) => {
    setSelection((prev) => {
      const next = new Set(prev)
      if (next.has(r.gaHandle)) next.delete(r.gaHandle)
      else next.add(r.gaHandle)
      return next
    })
  }, [])

  const toggleSelectAll = useCallback(() => {
    setSelection((prev) => {
      const next = new Set(prev)
      if (
        selectableVisible.length > 0 &&
        selectableVisible.every((r) => prev.has(r.gaHandle))
      ) {
        for (const r of selectableVisible) next.delete(r.gaHandle)
        return next
      }
      for (const r of selectableVisible) next.add(r.gaHandle)
      return next
    })
  }, [selectableVisible])

  // What "Trash selected" would actually do.  The selection deliberately
  // survives filter changes, so the bar has to SAY how much of it is off
  // screen rather than look like it lost rows.
  const impact = useMemo(() => {
    const rows = relics.filter(
      (r) =>
        selection.has(r.gaHandle) && isSellable(r) && !trashed.has(r.gaHandle),
    )
    const byTier = { in_use: 0, backup: 0, contender: 0, dead: 0, unknown: 0 }
    let murk = 0
    for (const r of rows) {
      murk += sellValue(effectCountOf(r.effects), r.isDeep)
      const t = usage.get(r.gaHandle)?.tier
      if (t) byTier[t] += 1
      else byTier.unknown += 1
    }
    return { rows, murk, byTier }
  }, [relics, selection, isSellable, trashed, usage])

  function trashSelected() {
    // One store write for the whole selection — see addSells.
    addSells(
      slotIndex,
      impact.rows.map((r) => ({ gaHandle: r.gaHandle, meta: metaFor(r) })),
    )
    setSelection(new Set())
    setConfirmOpen(false)
  }

  const selectedCount = selection.size
  const hiddenCount = selectedCount - selectedVisibleCount

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
              usageKnown={usageKnown}
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
                    Keepers first
                  </DropdownMenuRadioItem>
                  <DropdownMenuRadioItem value="least">
                    Dead weight first
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
              {pending.murkDelta !== 0 && (
                <span className="text-sky-600 dark:text-sky-500">
                  ({pending.murkDelta > 0 ? "+" : "−"}
                  {formatMurks(Math.abs(pending.murkDelta))} from purchases)
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

      {usageUnavailable && (
        <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-600 dark:text-amber-400">
          Build usage could not be loaded, so relics have no cull tier and the
          tier filter is off. Everything else on this page still works.
        </p>
      )}

      {visible.length === 0 ? (
        <p className="text-sm text-muted-foreground py-8 text-center">
          No relics match the current filters.
        </p>
      ) : (
        <RelicTable
          rows={visible}
          usage={usage}
          buildsById={buildsById}
          usageKnown={usageKnown}
          usageUnavailable={usageUnavailable}
          effectMap={effectMap}
          selection={selection}
          trashed={trashed}
          isSellable={isSellable}
          isFavorite={effectiveFavorite}
          headerChecked={
            allSelected
              ? true
              : selectedVisibleCount > 0
                ? "indeterminate"
                : false
          }
          headerLabel={
            allSelected
              ? `Clear selection of ${selectableVisible.length.toLocaleString()} relics`
              : `Select all ${selectableVisible.length.toLocaleString()} matching`
          }
          onToggleSelectAll={toggleSelectAll}
          onToggleSelect={toggleSelect}
          onTrash={trash}
          onRestore={restore}
          onToggleFavorite={toggleFavorite}
        />
      )}

      {/* Bulk-action bar for the transient selection. */}
      {selectedCount > 0 && (
        <div className="sticky bottom-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card p-3 shadow-lg">
          <span className="text-sm">
            <strong>{selectedCount.toLocaleString()}</strong> selected
            <span className="text-muted-foreground">
              {" · "}
              {selectedVisibleCount.toLocaleString()} shown by current filter
              {hiddenCount > 0 && ` · ${hiddenCount.toLocaleString()} hidden`}
            </span>
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
              onClick={() => setConfirmOpen(true)}
              disabled={impact.rows.length === 0}
              className="gap-1.5"
            >
              <Trash2 className="h-4 w-4" />
              Trash {impact.rows.length.toLocaleString()} selected
            </Button>
          </div>
        </div>
      )}

      {/* Bulk trash asks first; a single-row trash stays instant. */}
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              Trash {impact.rows.length.toLocaleString()} relics?
            </DialogTitle>
            <DialogDescription asChild>
              <div className="space-y-2">
                <p>
                  You get back <strong>{formatMurks(impact.murk)} Murk</strong>.
                </p>
                <ul className="list-disc space-y-0.5 pl-5">
                  {TIER_ORDER.map((t) =>
                    impact.byTier[t] > 0 ? (
                      <li key={t}>
                        <strong>{impact.byTier[t].toLocaleString()}</strong>{" "}
                        {TIER_META[t].label.toLowerCase()}
                      </li>
                    ) : null,
                  )}
                  {impact.byTier.unknown > 0 && (
                    <li>
                      <strong>{impact.byTier.unknown.toLocaleString()}</strong>{" "}
                      with no usage answer yet
                    </li>
                  )}
                </ul>
                <p>This is undoable from the Changes panel until you export.</p>
              </div>
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={trashSelected}>
              Trash {impact.rows.length.toLocaleString()} relics
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
