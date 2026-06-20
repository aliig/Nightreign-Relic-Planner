/**
 * Shared constants and components for displaying relics and their
 * effects/curses consistently across inventory, optimizer, and other views.
 */
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"

export const COLOR_HEX: Record<string, string> = {
  Red: "#FF4444",
  Blue: "#4488FF",
  Yellow: "#B8860B",
  Green: "#44BB44",
  White: "#AAAAAA",
}

export const DEEP_COLOR = "#8B6FC0"

export const EMPTY_EFFECT = 4294967295

/** Canonical relic colors, in display order. Single source for color filters. */
export const RELIC_COLORS: string[] = ["Red", "Blue", "Yellow", "Green"]

/** Canonical relic tiers, best → smallest. Single source for tier filters. */
export const RELIC_TIERS: string[] = ["Grand", "Polished", "Delicate"]

/**
 * Per-copy practical magnitude (e.g. "+15%") per effect id, populated as a side
 * effect of buildEffectMap (which every effect-rendering surface calls). Lets
 * the shared EffectPill show a magnitude tooltip without threading the data
 * through every caller.
 */
const effectBonusById = new Map<number, string>()

/** Per-copy magnitude string for an effect id, if the game data has one. */
export function effectBonus(id: number): string | undefined {
  return effectBonusById.get(id)
}

/** Build the effect ID → display name map, including alias IDs. */
export function buildEffectMap(effectsData: unknown[]): Map<number, string> {
  const m = new Map<number, string>()
  for (const raw of effectsData ?? []) {
    const e = raw as Record<string, unknown>
    if (typeof e.id === "number" && typeof e.name === "string") {
      m.set(e.id, e.name)
      const bonus = typeof e.bonus_display === "string" ? e.bonus_display : null
      if (bonus) effectBonusById.set(e.id, bonus)
      if (Array.isArray(e.alias_ids)) {
        for (const aliasId of e.alias_ids as unknown[]) {
          if (typeof aliasId === "number") {
            m.set(aliasId, e.name)
            if (bonus) effectBonusById.set(aliasId, bonus)
          }
        }
      }
    }
  }
  return m
}

/** Colored relic name + tier/color/deep metadata line. */
export function RelicNameCell({
  name,
  color,
  tier,
  isDeep,
}: {
  name: string
  color: string
  tier: string
  isDeep: boolean
}) {
  const hex = COLOR_HEX[color] ?? "#AAAAAA"
  return (
    <div>
      <span className="font-medium" style={{ color: hex }}>
        {name}
      </span>
      <div className="text-xs text-muted-foreground mt-0.5">
        {tier} · {color}
        {isDeep ? (
          <>
            {" · "}
            <span style={{ color: DEEP_COLOR }}>Deep</span>
          </>
        ) : null}
      </div>
    </div>
  )
}

/** A single effect or curse pill; shows its per-copy magnitude on hover. */
export function EffectPill({
  name,
  isCurse,
  bonus,
}: {
  name: string
  isCurse: boolean
  /** Per-copy practical magnitude (e.g. "+15%") — adds a hover tooltip. */
  bonus?: string
}) {
  const pill = (
    <span
      className={`inline-block rounded px-1.5 py-0.5 text-xs ${
        isCurse
          ? "bg-destructive/10 text-destructive"
          : "bg-muted text-muted-foreground"
      } ${bonus ? "cursor-help underline decoration-dotted decoration-muted-foreground/40 underline-offset-2" : ""}`}
    >
      {name}
    </span>
  )
  if (!bonus) return pill
  return (
    <Tooltip>
      <TooltipTrigger asChild>{pill}</TooltipTrigger>
      <TooltipContent>
        <span className="font-medium">{bonus}</span>
        <span className="text-muted-foreground"> per copy</span>
      </TooltipContent>
    </Tooltip>
  )
}

/** Renders a column of effect or curse pills from raw IDs + an effect map. */
export function EffectList({
  effectIds,
  isCurse,
  effectMap,
}: {
  effectIds: number[]
  isCurse: boolean
  effectMap: Map<number, string>
}) {
  const items: { id: number; name: string }[] = []
  for (const id of effectIds) {
    if (id === 0 || id === EMPTY_EFFECT) continue
    const name = effectMap.get(id)
    if (name) items.push({ id, name })
  }
  if (items.length === 0) return null
  return (
    <div className="flex flex-col gap-1">
      {items.map(({ id, name }, i) => (
        <EffectPill
          key={`${id}-${i}`}
          name={name}
          isCurse={isCurse}
          bonus={effectBonus(id)}
        />
      ))}
    </div>
  )
}
