/**
 * Relic sell values (Murk), mirroring nrplanner/writer.py sell_value().
 * In-game prices by number of properties (effects); Deep relics sell for double.
 */
const EMPTY_EFFECT = 4294967295 // 0xFFFFFFFF
const SELL_BY_EFFECT_COUNT: Record<number, number> = { 1: 150, 2: 350, 3: 550 }

export function effectCountOf(effects: number[]): number {
  return effects.filter((e) => e !== EMPTY_EFFECT && e !== 0).length
}

export function sellValue(effectCount: number, isDeep: boolean): number {
  const clamped = Math.min(Math.max(effectCount, 1), 3)
  return SELL_BY_EFFECT_COUNT[clamped] * (isDeep ? 2 : 1)
}

export function formatMurks(n: number): string {
  return n.toLocaleString()
}
