/** A relic's lifecycle status, derived from its in-game equip + build usage. */
export type RelicStatus = "active" | "stale" | "bench" | "unused"

/**
 * Combine whether a relic is equipped in-game with whether any build uses it
 * into a single triage signal:
 *  - active : equipped AND used  → keep
 *  - stale  : equipped, unused   → review/replace (the in-game cleanup target)
 *  - bench  : unused-equip, used → a build wants it; equip it
 *  - unused : not equipped, unused → safe to sell
 */
export function relicStatus(equipped: boolean, used: boolean): RelicStatus {
  if (equipped) return used ? "active" : "stale"
  return used ? "bench" : "unused"
}
