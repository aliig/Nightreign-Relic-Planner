/**
 * The inventory's one vocabulary for "how disposable is this relic?".
 *
 * Replaces the old Active/Stale/Bench/Unused status, which mixed two unrelated
 * questions — is it equipped in-game, and does a build want it — into one
 * badge. Equipped is now a row marker; this axis is purely about culling.
 *
 * Uncertainty is deliberately NOT a fifth tier. A relic can be rank-1 in one
 * build and wanted by a build whose results are out of date; an enum would
 * force a false choice between two true statements. It renders as a "?" on the
 * badge instead. Note the invariant the server guarantees: `dead` means no
 * build could want it, so dead is never uncertain.
 */
import type { RelicUsage } from "@/hooks/useRelicUsage"

export type RelicTier = RelicUsage["tier"]

/** Most valuable first — the order tier-based sorting walks. */
export const TIER_ORDER: RelicTier[] = ["in_use", "backup", "contender", "dead"]

export const TIER_META: Record<
  RelicTier,
  { label: string; cls: string; hint: string }
> = {
  in_use: {
    label: "In use",
    cls: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
    hint: "A build's current best layout places this relic — keep it.",
  },
  backup: {
    label: "Backup",
    cls: "border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-400",
    hint: "Only a build's alternative layouts use this — a fallback, not a keeper.",
  },
  contender: {
    label: "Contender",
    cls: "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400",
    hint: "Placed nowhere right now, but a build could still score it.",
  },
  dead: {
    label: "Dead weight",
    cls: "border-border bg-muted text-muted-foreground",
    hint: "No build you have could use this relic at any score — safe to sell.",
  },
}

export const UNCERTAIN_HINT =
  "A build that could want this relic is out of date. Re-optimize to be sure."

/** Rank for sorting; lower = more worth keeping. */
export function tierRank(tier: RelicTier): number {
  return TIER_ORDER.indexOf(tier)
}
