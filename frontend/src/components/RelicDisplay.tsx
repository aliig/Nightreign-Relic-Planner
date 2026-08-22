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

/**
 * Per-Nightfarer applicability per effect id, populated alongside the bonus
 * map. Mirrors AttachEffectParam's allow* flags (see nrplanner/constants.py).
 */
const effectAllowById = new Map<number, Record<string, boolean>>()

/**
 * Whether an effect actually does anything for `character`.
 *
 * The game greys out effects the active Nightfarer cannot use — someone
 * else's character-exclusive effect, or an armament-skill swap whose ash of
 * war doesn't fit their starting weapon (Seppuku on colossal-wielding Raider,
 * the sorcery variants without Recluse's staff, the incantation variants
 * without Revenant's seal). The optimizer treats these as absent, so the UI
 * shows them the same way.
 *
 * Unknown effect or no character selected → usable, matching the backend's
 * permissive default.
 */
export function isEffectUsableBy(
  id: number,
  character?: string | null,
): boolean {
  if (!character || character === "All") return true
  const allow = effectAllowById.get(id)
  if (!allow) return true
  return allow[character] !== false
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
      const allow =
        e.allow_per_character && typeof e.allow_per_character === "object"
          ? (e.allow_per_character as Record<string, boolean>)
          : null
      if (allow) effectAllowById.set(e.id, allow)
      if (Array.isArray(e.alias_ids)) {
        for (const aliasId of e.alias_ids as unknown[]) {
          if (typeof aliasId === "number") {
            m.set(aliasId, e.name)
            if (bonus) effectBonusById.set(aliasId, bonus)
            if (allow) effectAllowById.set(aliasId, allow)
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
  inert,
  inertCharacter,
}: {
  name: string
  isCurse: boolean
  /** Per-copy practical magnitude (e.g. "+15%") — adds a hover tooltip. */
  bonus?: string
  /** Greyed out in-game for the active Nightfarer — does nothing. */
  inert?: boolean
  /** Name shown in the inert tooltip. */
  inertCharacter?: string | null
}) {
  const pill = (
    <span
      className={`inline-block rounded px-1.5 py-0.5 text-xs ${
        inert
          ? "bg-muted/40 text-muted-foreground/50 line-through decoration-muted-foreground/40"
          : isCurse
            ? "bg-destructive/10 text-destructive"
            : "bg-muted text-muted-foreground"
      } ${bonus || inert ? "cursor-help" : ""} ${
        bonus && !inert
          ? "underline decoration-dotted decoration-muted-foreground/40 underline-offset-2"
          : ""
      }`}
    >
      {name}
    </span>
  )
  if (inert) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>{pill}</TooltipTrigger>
        <TooltipContent className="max-w-72">
          <span className="font-medium">
            Does nothing{inertCharacter ? ` for ${inertCharacter}` : ""}
          </span>
          <div className="text-muted-foreground">
            The game greys this out — their starting armament can't use it, so
            it's ignored when scoring and never blocks another effect.
          </div>
        </TooltipContent>
      </Tooltip>
    )
  }
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
  character,
}: {
  effectIds: number[]
  isCurse: boolean
  effectMap: Map<number, string>
  /** When set, effects this Nightfarer can't use render greyed out. */
  character?: string | null
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
          inert={!isEffectUsableBy(id, character)}
          inertCharacter={character}
        />
      ))}
    </div>
  )
}
