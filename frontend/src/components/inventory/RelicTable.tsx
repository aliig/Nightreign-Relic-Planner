import { useVirtualizer } from "@tanstack/react-virtual"
import { useRef } from "react"

import { Checkbox } from "@/components/ui/checkbox"
import type { BuildUsageInfo, RelicUsage } from "@/hooks/useRelicUsage"
import { RelicRow } from "./RelicRow"
import type { ManagedRelic } from "./types"

/**
 * One grid template shared by the header row and every body row, so the
 * columns line up even though each `<tr>` lays itself out independently.
 */
export const GRID_TEMPLATE =
  "2.5rem minmax(11rem, 1.5fr) 9rem minmax(14rem, 2fr) 3rem 3rem"

export type RelicTableProps = {
  rows: ManagedRelic[]
  usage: Map<number, RelicUsage>
  buildsById: Map<string, BuildUsageInfo>
  usageKnown: boolean
  usageUnavailable: boolean
  effectMap: Map<number, string>
  selection: Set<number>
  trashed: Set<number>
  isSellable: (r: ManagedRelic) => boolean
  isFavorite: (r: ManagedRelic) => boolean
  headerChecked: boolean | "indeterminate"
  headerLabel: string
  onToggleSelectAll: () => void
  onToggleSelect: (relic: ManagedRelic) => void
  onTrash: (relic: ManagedRelic) => void
  onRestore: (relic: ManagedRelic) => void
  onToggleFavorite: (relic: ManagedRelic) => void
}

/**
 * The inventory list, virtualized.
 *
 * At the relic cap the old `visible.map(...)` mounted ~2,000 rows — roughly
 * 18,000 Radix subtrees — at once. Only the ~30 rows in view (plus overscan)
 * are mounted here. Row height varies with effect and curse count, so each
 * mounted row measures itself; the estimate only has to be close.
 *
 * It stays a real `<table>`: `display: block/grid` moves the layout to CSS grid
 * (which is what lets the body rows be absolutely positioned) without giving up
 * table semantics for screen readers, and a sticky `<thead>` keeps the header
 * in view while the body scrolls.
 */
export function RelicTable({
  rows,
  usage,
  buildsById,
  usageKnown,
  usageUnavailable,
  effectMap,
  selection,
  trashed,
  isSellable,
  isFavorite,
  headerChecked,
  headerLabel,
  onToggleSelectAll,
  onToggleSelect,
  onTrash,
  onRestore,
  onToggleFavorite,
}: RelicTableProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 84,
    overscan: 8,
  })

  return (
    <div
      ref={scrollRef}
      className="max-h-[70vh] overflow-y-auto overflow-x-hidden rounded-md border"
    >
      <table className="block w-full">
        <thead className="sticky top-0 z-10 block bg-muted">
          <tr
            className="grid items-center gap-3 border-b px-4 py-2 text-left text-xs font-medium text-muted-foreground"
            style={{ gridTemplateColumns: GRID_TEMPLATE }}
          >
            <th scope="col" className="font-medium">
              <Checkbox
                checked={headerChecked}
                onCheckedChange={onToggleSelectAll}
                aria-label={headerLabel}
                title={headerLabel}
              />
            </th>
            <th scope="col" className="font-medium">
              Relic
            </th>
            <th scope="col" className="font-medium">
              Tier
            </th>
            <th scope="col" className="font-medium">
              Effects
            </th>
            <th scope="col" className="text-center font-medium">
              Mark
            </th>
            <th scope="col" className="text-center font-medium">
              Trash
            </th>
          </tr>
        </thead>
        {/* The virtualizer's total height lives on the body, so the scroll
            container scrolls as if every row were mounted. */}
        <tbody
          className="relative block"
          style={{ height: `${virtualizer.getTotalSize()}px` }}
        >
          {virtualizer.getVirtualItems().map((item) => {
            const relic = rows[item.index]
            return (
              <RelicRow
                key={relic.key}
                measureRef={virtualizer.measureElement}
                index={item.index}
                offset={item.start}
                relic={relic}
                usage={usage.get(relic.gaHandle)}
                buildsById={buildsById}
                usageKnown={usageKnown}
                usageUnavailable={usageUnavailable}
                selected={selection.has(relic.gaHandle)}
                trashed={trashed.has(relic.gaHandle)}
                sellable={isSellable(relic)}
                favorite={isFavorite(relic)}
                effectMap={effectMap}
                onToggleSelect={onToggleSelect}
                onTrash={onTrash}
                onRestore={onRestore}
                onToggleFavorite={onToggleFavorite}
              />
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
