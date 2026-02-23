/**
 * Shared constants and components for displaying relics and their
 * effects/curses consistently across inventory, optimizer, and other views.
 */

export const COLOR_HEX: Record<string, string> = {
  Red: "#FF4444",
  Blue: "#4488FF",
  Yellow: "#B8860B",
  Green: "#44BB44",
  White: "#AAAAAA",
}

export const DEEP_COLOR = "#8B6FC0"

export const EMPTY_EFFECT = 4294967295

/** Build the effect ID → display name map, including alias IDs. */
export function buildEffectMap(effectsData: unknown[]): Map<number, string> {
  const m = new Map<number, string>()
  for (const raw of effectsData ?? []) {
    const e = raw as Record<string, unknown>
    if (typeof e.id === "number" && typeof e.name === "string") {
      m.set(e.id, e.name)
      if (Array.isArray(e["alias_ids"])) {
        for (const aliasId of e["alias_ids"] as unknown[]) {
          if (typeof aliasId === "number") m.set(aliasId, e.name)
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

/** A single effect or curse pill. */
export function EffectPill({
  name,
  isCurse,
}: {
  name: string
  isCurse: boolean
}) {
  return (
    <span
      className={`inline-block rounded px-1.5 py-0.5 text-xs ${
        isCurse
          ? "bg-destructive/10 text-destructive"
          : "bg-muted text-muted-foreground"
      }`}
    >
      {name}
    </span>
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
  const names: string[] = []
  for (const id of effectIds) {
    if (id === 0 || id === EMPTY_EFFECT) continue
    const name = effectMap.get(id)
    if (name) names.push(name)
  }
  if (names.length === 0) return null
  return (
    <div className="flex flex-col gap-1">
      {names.map((name) => (
        <EffectPill key={name} name={name} isCurse={isCurse} />
      ))}
    </div>
  )
}
