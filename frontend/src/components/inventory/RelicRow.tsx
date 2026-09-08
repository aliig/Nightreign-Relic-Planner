import { Link } from "@tanstack/react-router"
import { Lock, Pin, RotateCcw, Star, Trash2 } from "lucide-react"
import { memo } from "react"

import { EffectList, RelicNameCell } from "@/components/RelicDisplay"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { BuildUsageInfo, RelicUsage } from "@/hooks/useRelicUsage"
import { cn } from "@/lib/utils"
import { GRID_TEMPLATE } from "./RelicTable"
import { TIER_META, UNCERTAIN_HINT } from "./tiers"
import { isUniqueRelic, type ManagedRelic } from "./types"

export type RelicRowProps = {
  /** The virtualizer measures each mounted row — heights vary with effect and
   *  curse count, so the estimate is only a starting point. */
  measureRef: (el: HTMLElement | null) => void
  index: number
  offset: number
  relic: ManagedRelic
  usage: RelicUsage | undefined
  buildsById: Map<string, BuildUsageInfo>
  usageKnown: boolean
  selected: boolean
  trashed: boolean
  sellable: boolean
  favorite: boolean
  effectMap: Map<number, string>
  onToggleSelect: (relic: ManagedRelic) => void
  onTrash: (relic: ManagedRelic) => void
  onRestore: (relic: ManagedRelic) => void
  onToggleFavorite: (relic: ManagedRelic) => void
}

/**
 * One inventory row.
 *
 * `memo`'d and given only primitives plus stable references: at the 1,950
 * relic cap a row holds a checkbox, two tooltips, a popover and up to six
 * effect pills, all Radix subtrees. Without this, ticking one checkbox
 * re-rendered every mounted row. It was an inline closure inside the table
 * before, which is why it could not be memoized where it stood.
 */
function RelicRowInner({
  measureRef,
  index,
  offset,
  relic,
  usage,
  buildsById,
  usageKnown,
  selected,
  trashed,
  sellable,
  favorite,
  effectMap,
  onToggleSelect,
  onTrash,
  onRestore,
  onToggleFavorite,
}: RelicRowProps) {
  const uses = usage?.used_by ?? []
  const count = uses.length
  const tier = usage?.tier
  const isUnique = isUniqueRelic(relic.realId)
  const lockReason = relic.incoming
    ? "Staged purchase — undo it from the Changes panel"
    : relic.equipped
      ? "Equipped — can't trash"
      : isUnique
        ? "Unique relic — can't be re-acquired, so it's locked"
        : favorite
          ? "Bookmarked — un-bookmark to trash"
          : undefined

  // Effects and curses share one column: curses stack beneath the effects
  // (rendered red), so the action icons stay in view.
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
    <tr
      ref={measureRef}
      data-index={index}
      data-state={selected ? "selected" : undefined}
      className={cn(
        "absolute left-0 top-0 grid w-full items-center gap-3 border-b px-4 py-2 text-sm",
        trashed && "opacity-50",
        selected && "bg-muted/50",
      )}
      style={{
        gridTemplateColumns: GRID_TEMPLATE,
        transform: `translateY(${offset}px)`,
      }}
    >
      <td>
        {sellable ? (
          <Checkbox
            checked={selected}
            disabled={trashed}
            onCheckedChange={() => onToggleSelect(relic)}
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
      </td>

      <td className="min-w-0">
        <span
          className={cn(
            trashed && "line-through",
            "inline-flex items-center gap-1.5",
          )}
        >
          <RelicNameCell
            name={relic.name}
            color={relic.color}
            tier={relic.tier}
            isDeep={relic.isDeep}
          />
          {relic.equipped && (
            <span
              role="img"
              title="Equipped in-game"
              aria-label="Equipped in-game"
              className="inline-flex text-muted-foreground"
            >
              <Pin className="h-3.5 w-3.5" />
            </span>
          )}
          {relic.incoming && (
            <Badge
              className="h-4 px-1.5 py-0 text-[10px] bg-sky-600 text-white hover:bg-sky-600"
              title="Staged Relic Rites purchase — not in your save until you export"
            >
              Incoming
            </Badge>
          )}
        </span>
      </td>

      <td className="flex flex-col items-start gap-1">
        {!usageKnown || !tier ? (
          <span className="inline-block h-5 w-20 animate-pulse rounded bg-muted" />
        ) : (
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge
                variant="outline"
                tabIndex={0}
                className={cn(
                  "cursor-help font-normal focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  TIER_META[tier].cls,
                )}
              >
                {TIER_META[tier].label}
                {usage?.uncertain && <span className="ml-1 font-bold">?</span>}
              </Badge>
            </TooltipTrigger>
            <TooltipContent side="right" className="max-w-[15rem]">
              {TIER_META[tier].hint}
              {usage?.uncertain && ` ${UNCERTAIN_HINT}`}
            </TooltipContent>
          </Tooltip>
        )}
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
                Used by {count} build{count !== 1 ? "s" : ""} — open in the
                Optimizer:
              </p>
              <ul className="space-y-0.5">
                {uses.map((u) => (
                  <li key={u.build_id}>
                    <Link
                      to="/builds/$buildId/optimize"
                      params={{ buildId: u.build_id }}
                      className="block truncate rounded px-1.5 py-1 text-sm hover:bg-accent hover:text-accent-foreground"
                    >
                      {buildsById.get(u.build_id)?.name ?? "Unnamed build"}
                      <span className="ml-1 text-xs text-muted-foreground">
                        {u.rank === 1 ? "(best)" : `(#${u.rank})`}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            </PopoverContent>
          </Popover>
        )}
      </td>

      <td className="min-w-0">
        {effectsCell || cursesCell ? (
          <div className="flex flex-col gap-1.5">
            {effectsCell}
            {cursesCell}
          </div>
        ) : (
          <span className="text-xs text-muted-foreground italic">—</span>
        )}
      </td>

      <td className="text-center">
        <button
          type="button"
          onClick={() => onToggleFavorite(relic)}
          disabled={relic.incoming}
          title={
            relic.incoming
              ? "Staged purchase — bookmark it after exporting"
              : favorite
                ? "Remove bookmark"
                : "Bookmark"
          }
          aria-label={favorite ? "Remove bookmark" : "Bookmark"}
          className="inline-flex p-1 rounded hover:bg-accent disabled:opacity-30 disabled:cursor-not-allowed"
        >
          <Star
            className={`h-4 w-4 ${
              favorite
                ? "fill-amber-400 text-amber-500"
                : "text-muted-foreground"
            }`}
          />
        </button>
      </td>

      <td className="text-center">
        {trashed ? (
          <button
            type="button"
            onClick={() => onRestore(relic)}
            title="Restore relic"
            aria-label={`Restore ${relic.name}`}
            className="inline-flex p-1 rounded hover:bg-accent"
          >
            <RotateCcw className="h-4 w-4 text-muted-foreground" />
          </button>
        ) : (
          <button
            type="button"
            onClick={() => onTrash(relic)}
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
      </td>
    </tr>
  )
}

export const RelicRow = memo(RelicRowInner)
