import type { LucideIcon } from "lucide-react"
import {
  AlertTriangle,
  ArrowLeftRight,
  Sparkles,
  TrendingDown,
  TrendingUp,
} from "lucide-react"

import type { BuildChange, RelicRef } from "@/client"

export type ChangeTone = "up" | "down" | "neutral" | "warn"

export interface ChangeDescription {
  tone: ChangeTone
  icon: LucideIcon
  /** Short, plain-language verdict, e.g. "23% stronger" / "rearranged, same strength". */
  headline: string
  /** Which relics moved in/out of the best setup — capped to 2 names + an "extra" count. */
  relics?: { verb: string; names: string[]; extra: number }
  /** Raw optimizer score (internal unit) — for a hover tooltip only, never shown inline. */
  rawScore?: { before: number; after: number; delta: number }
  /** False when the delta came from a truncated (non-exhaustive) search. */
  reliable: boolean
  /** Tailwind classes for inline colored text (badge / list rows). */
  textClass: string
  /** Tailwind classes for a bordered banner box. */
  boxClass: string
}

const TONE_TEXT: Record<ChangeTone, string> = {
  up: "text-green-600 dark:text-green-400",
  down: "text-red-500",
  neutral: "text-muted-foreground",
  warn: "text-amber-600 dark:text-amber-400",
}

const TONE_BOX: Record<ChangeTone, string> = {
  up: "border-green-500/40 bg-green-500/10 text-green-700 dark:text-green-400",
  down: "border-destructive/40 bg-destructive/10 text-destructive",
  neutral: "border-muted-foreground/30 bg-muted/40 text-foreground",
  warn: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400",
}

/**
 * Relative score change vs the last save, as a percent string ("23%").
 *
 * The percent is a fair within-build comparison: the build's config and weights
 * are unchanged across saves, so only the inventory moved. Returns null when
 * there's no usable baseline (`best_before` missing or <= 0) or the change rounds
 * to 0% — callers fall back to a non-numeric verdict in those cases.
 */
function percentLabel(
  before?: number | null,
  after?: number | null,
): string | null {
  if (before == null || after == null || before <= 0) return null
  const pct = Math.round(((after - before) / before) * 100)
  if (pct === 0) return null
  return `${Math.abs(pct)}%`
}

function relicDetail(
  verb: string,
  refs: RelicRef[] | undefined,
): ChangeDescription["relics"] {
  const names = (refs ?? [])
    .map((r) => r.name)
    .filter((n): n is string => !!n && n.length > 0)
  if (names.length === 0) return undefined
  return {
    verb,
    names: names.slice(0, 2),
    extra: Math.max(0, names.length - 2),
  }
}

/**
 * Turn a BuildChange into human, relic-aware presentation — a plain verdict, a
 * relative % vs the last save, and which relics moved — replacing the opaque
 * "+91 pts". Raw points are exposed only via `rawScore` (for a tooltip).
 *
 * Returns null for changes not worth surfacing (unchanged / first-ever optimize).
 * Callers apply their own policy on `change.cause` — e.g. the post-upload list
 * only wants relic-caused changes, while the at-a-glance badge shows any.
 */
export function describeBuildChange(
  change?: BuildChange | null,
): ChangeDescription | null {
  if (!change) return null
  const { status } = change
  if (status === "unchanged" || status === "new") return null

  const before = change.best_before ?? null
  const after = change.best_after ?? null
  const rawScore =
    before != null && after != null
      ? { before, after, delta: change.delta ?? after - before }
      : undefined

  let tone: ChangeTone
  let icon: LucideIcon
  let headline: string
  let relics: ChangeDescription["relics"]

  if (status === "improved") {
    tone = "up"
    icon = TrendingUp
    const pct = percentLabel(before, after)
    // No usable baseline % means the build went from ~nothing to something.
    headline = pct ? `${pct} stronger` : "newly viable"
    relics = relicDetail("now uses", change.entered)
  } else if (status === "degraded") {
    tone = "down"
    icon = TrendingDown
    const pct = percentLabel(before, after)
    headline = pct ? `${pct} weaker` : "weaker"
    relics = relicDetail("lost", change.left)
  } else if (status === "reordered") {
    tone = "neutral"
    icon = ArrowLeftRight
    headline = "rearranged, same strength"
    relics = relicDetail("swaps in", change.entered)
  } else if (status === "broken_pin") {
    tone = "warn"
    icon = AlertTriangle
    headline = "a pinned relic left your save"
    relics = relicDetail("pin lost", change.pinned_removed)
  } else {
    // potentially_affected — cheap relic-diff flag, no precise layout computed yet.
    tone = "neutral"
    icon = Sparkles
    const n = change.relevant_added ?? 0
    headline = n
      ? `${n} new relic${n === 1 ? "" : "s"} may help`
      : "new relics may help"
  }

  return {
    tone,
    icon,
    headline,
    relics,
    rawScore,
    reliable: change.reliable !== false,
    textClass: TONE_TEXT[tone],
    boxClass: TONE_BOX[tone],
  }
}

/** "Crimson Whetblade, Stalwart Horn +1 more" — for tooltips/aria and compact rows. */
export function relicSummary(relics: ChangeDescription["relics"]): string {
  if (!relics) return ""
  const joined = relics.names.join(", ")
  return relics.extra > 0 ? `${joined} +${relics.extra} more` : joined
}

/** "387 → 478 pts" — raw optimizer score for a hover tooltip. */
export function rawScoreTooltip(
  rawScore: ChangeDescription["rawScore"],
): string | undefined {
  if (!rawScore) return undefined
  return `${rawScore.before} → ${rawScore.after} pts`
}
