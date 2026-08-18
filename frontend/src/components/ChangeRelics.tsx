/**
 * Presentation for the relics named in a BuildChange.
 *
 * A bare relic name ("Polished Burning Scene") tells the player nothing — the
 * name encodes tier and pool, not what the relic *does*, and two relics with the
 * same name can carry completely different effects. So every relic renders as a
 * chip in its own color with a hover card: tier · color · Deep, its effects and
 * curses as the same pills the inventory uses (magnitudes included), and whether
 * it is still in the save.
 *
 * Shared by all four change surfaces (upload summary, builds list, optimize
 * banner, optimize tracker) so they cannot drift apart.
 */
import type { RelicRef } from "@/client"
import {
  COLOR_HEX,
  DEEP_COLOR,
  EffectPill,
  EMPTY_EFFECT,
  effectBonus,
} from "@/components/RelicDisplay"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { ChangeRelicGroup, ChangeRelicKind } from "@/lib/buildChange"

/** Label color per group — gone/pin read as loss, entered as gain. */
const KIND_LABEL_CLASS: Record<ChangeRelicKind, string> = {
  entered: "text-green-600 dark:text-green-400",
  benched: "text-muted-foreground",
  gone: "text-destructive",
  pin: "text-amber-600 dark:text-amber-400",
}

function relicMeta(relic: RelicRef): string {
  const bits = [relic.tier, relic.color].filter(
    (b): b is string => !!b && b.length > 0,
  )
  return bits.join(" · ")
}

function pills(ids: number[] | undefined, effectMap: Map<number, string>) {
  const out: { id: number; name: string }[] = []
  for (const id of ids ?? []) {
    if (id === 0 || id === EMPTY_EFFECT) continue
    const name = effectMap.get(id)
    if (name) out.push({ id, name })
  }
  return out
}

/** Hover card body: what this relic actually is. */
function RelicDetail({
  relic,
  effectMap,
}: {
  relic: RelicRef
  effectMap: Map<number, string>
}) {
  const effects = pills(relic.effects, effectMap)
  const curses = pills(relic.curses, effectMap)
  const hex = COLOR_HEX[relic.color ?? ""] ?? "#AAAAAA"
  return (
    <div className="space-y-1.5">
      <div>
        <div className="font-medium" style={{ color: hex }}>
          {relic.name || `Relic ${relic.real_id}`}
        </div>
        <div className="text-xs text-muted-foreground">
          {relicMeta(relic)}
          {relic.is_deep ? (
            <>
              {relicMeta(relic) ? " · " : ""}
              <span style={{ color: DEEP_COLOR }}>Deep</span>
            </>
          ) : null}
        </div>
      </div>

      {effects.length + curses.length > 0 ? (
        <div className="flex flex-col gap-1">
          {effects.map(({ id, name }, i) => (
            <EffectPill
              key={`e-${id}-${i}`}
              name={name}
              isCurse={false}
              bonus={effectBonus(id)}
            />
          ))}
          {curses.map(({ id, name }, i) => (
            <EffectPill key={`c-${id}-${i}`} name={name} isCurse={true} />
          ))}
        </div>
      ) : (
        <div className="text-xs text-muted-foreground">No effects.</div>
      )}

      {relic.staged ? (
        // Bought in Relic Rites: owned and paid for, but it reaches the save
        // file only on export — so it is never "in your save" yet.
        <div className="border-t pt-1 text-xs text-amber-600 dark:text-amber-400">
          Bought in Relic Rites — not exported to your save yet
        </div>
      ) : (
        relic.still_owned != null && (
          <div className="border-t pt-1 text-xs">
            {relic.still_owned ? (
              <span className="text-muted-foreground">Still in your save</span>
            ) : (
              <span className="text-destructive">No longer in your save</span>
            )}
          </div>
        )
      )}
    </div>
  )
}

/** One relic as a colored, hoverable chip. */
export function RelicRefChip({
  relic,
  effectMap,
}: {
  relic: RelicRef
  effectMap: Map<number, string>
}) {
  const hex = COLOR_HEX[relic.color ?? ""] ?? "#AAAAAA"
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={`inline-flex max-w-full cursor-help items-center gap-1 rounded border bg-background/60 px-1.5 py-0.5 text-xs${
            relic.staged ? " border-dashed border-amber-500/60" : ""
          }`}
        >
          <span
            className="h-2 w-2 shrink-0 rounded-full"
            style={{
              backgroundColor: hex,
              boxShadow: relic.is_deep
                ? `0 0 0 1.5px ${DEEP_COLOR}`
                : undefined,
            }}
          />
          <span className="truncate">
            {relic.name || `Relic ${relic.real_id}`}
          </span>
          {/* Dashed border + marker: this one is not in the save file yet. */}
          {relic.staged && (
            <span
              role="img"
              aria-label="not exported yet"
              className="shrink-0 text-amber-600 dark:text-amber-400"
            >
              ⤓
            </span>
          )}
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-[18rem]">
        <RelicDetail relic={relic} effectMap={effectMap} />
      </TooltipContent>
    </Tooltip>
  )
}

/**
 * A labeled row of relic chips ("No longer used  ● A  ● B  +2 more").
 *
 * `max` caps the chips inline; the overflow count keeps its own hover list so
 * nothing is silently hidden.
 */
export function ChangeRelicRow({
  group,
  effectMap,
  max = 3,
}: {
  group: ChangeRelicGroup
  effectMap: Map<number, string>
  max?: number
}) {
  const shown = group.relics.slice(0, max)
  const rest = group.relics.slice(max)
  return (
    <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs">
      <span className={`shrink-0 ${KIND_LABEL_CLASS[group.kind]}`}>
        {group.label}
      </span>
      {shown.map((r, i) => (
        <RelicRefChip
          key={`${r.real_id}-${i}`}
          relic={r}
          effectMap={effectMap}
        />
      ))}
      {rest.length > 0 && (
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="cursor-help text-muted-foreground underline decoration-dotted underline-offset-2">
              +{rest.length} more
            </span>
          </TooltipTrigger>
          <TooltipContent className="max-w-[18rem]">
            <div className="flex flex-col gap-1">
              {rest.map((r, i) => (
                <span key={`${r.real_id}-${i}`}>
                  {r.name || `Relic ${r.real_id}`}
                </span>
              ))}
            </div>
          </TooltipContent>
        </Tooltip>
      )}
    </div>
  )
}

/** Every group of a change, stacked. Renders nothing when no relics moved. */
export function ChangeRelicGroups({
  groups,
  effectMap,
  max,
}: {
  groups: ChangeRelicGroup[]
  effectMap: Map<number, string>
  max?: number
}) {
  if (groups.length === 0) return null
  return (
    <div className="mt-1 space-y-0.5">
      {groups.map((g) => (
        <ChangeRelicRow
          key={g.kind}
          group={g}
          effectMap={effectMap}
          max={max}
        />
      ))}
    </div>
  )
}
