import type { LucideIcon } from "lucide-react"
import {
  AlertTriangle,
  ArrowLeftRight,
  Sparkles,
  TrendingDown,
  TrendingUp,
} from "lucide-react"

import type { BuildChange, RelicRef } from "@/client"

/** What moved since the baseline (mirrors nrplanner.models.ChangeCause). */
export type ChangeCause = "relics" | "staged" | "build_edit" | "game_data"

export type ChangeTone = "up" | "down" | "neutral" | "warn"

/** Which of the four things that can happen to a relic this group describes. */
export type ChangeRelicKind = "entered" | "benched" | "gone" | "pin"

export interface ChangeRelicGroup {
  kind: ChangeRelicKind
  /** Row label, e.g. "Now uses" / "No longer in your save". */
  label: string
  relics: RelicRef[]
}

export interface ChangeDescription {
  tone: ChangeTone
  icon: LucideIcon
  /** Short, plain-language verdict, e.g. "23% stronger" / "best setup changed". */
  headline: string
  /** Which relics moved, split by what actually happened to them. */
  groups: ChangeRelicGroup[]
  /** One-line clarification of the verdict, when it would otherwise mislead. */
  note?: string
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
 * are unchanged across saves, so only the inventory moved. That holds only while
 * the scoring rules themselves hold still — see `change.comparable`, which the
 * caller must check before asking for a percent at all. Returns null when
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

/** Append a group, skipping it when no relic falls into it. */
function addGroup(
  groups: ChangeRelicGroup[],
  kind: ChangeRelicKind,
  label: string,
  refs: RelicRef[] | undefined,
): void {
  const relics = refs ?? []
  if (relics.length > 0) groups.push({ kind, label, relics })
}

/**
 * Split the relics that left the best layout by whether they left the *save*.
 *
 * `change.left` is a diff of two arrangements, not of two inventories: a relic
 * drops out of it whenever the best setup shifts around it, even though it is
 * still sitting in the save. The backend answers which is which per relic
 * (`still_owned`); where it didn't (older snapshots), we say the weaker,
 * true thing — "no longer used" — rather than claiming a loss we never checked.
 */
function splitLeft(refs: RelicRef[] | undefined): {
  gone: RelicRef[]
  benched: RelicRef[]
} {
  const gone: RelicRef[] = []
  const benched: RelicRef[] = []
  for (const r of refs ?? []) {
    if (r.still_owned === false) gone.push(r)
    else benched.push(r)
  }
  return { gone, benched }
}

/**
 * Turn a BuildChange into human, relic-aware presentation — a plain verdict, a
 * relative % vs the last save, and what happened to each relic that moved —
 * replacing the opaque "+91 pts". Raw points are exposed only via `rawScore`
 * (for a tooltip).
 *
 * Returns null for changes not worth surfacing (unchanged / first-ever optimize
 * / a same-strength rearrangement).
 * Callers apply their own policy on WHY the change happened — see
 * `isChangeNews`, which every narrating surface should gate on.
 *
 * A change the backend marked `comparable: false` keeps its relic groups but
 * loses every score claim (percent, direction, and the raw-points tooltip): the
 * two scores came from different optimizer/game-data versions and are not two
 * measurements of the same thing.
 */
export function describeBuildChange(
  change?: BuildChange | null,
): ChangeDescription | null {
  if (!change) return null
  const { status } = change
  // "reordered" is a same-strength shuffle: the optimizer found a different
  // arrangement worth the same points. That is noise dressed up as news — it
  // tells the user nothing to act on — so it is suppressed like "unchanged".
  // The backend re-baselines a reordered change silently to match; if only one
  // side suppressed it, the change would sit unread and freeze the yardstick
  // every later diff is measured against.
  if (status === "unchanged" || status === "new" || status === "reordered")
    return null

  const before = change.best_before ?? null
  const after = change.best_after ?? null
  // Scores from two different rule sets are not two measurements of the same
  // thing, so neither the headline percent NOR the raw-points hover may quote
  // them against each other. The layout diff below is unaffected: those relics
  // really did move.
  const comparable = change.comparable !== false
  const rawScore =
    comparable && before != null && after != null
      ? { before, after, delta: change.delta ?? after - before }
      : undefined

  const { gone, benched } = splitLeft(change.left)
  let tone: ChangeTone
  let icon: LucideIcon
  let headline: string
  let note: string | undefined
  const groups: ChangeRelicGroup[] = []

  if (!comparable && (status === "improved" || status === "degraded")) {
    // A cross-version comparison. Both of these statuses assert something about
    // strength — "stronger" and "weaker" alike — and an optimizer or game-data
    // bump can move a build's optimum on an inventory that never changed.
    // Report the movement, which is real, and make no claim about the
    // direction, which is not measurable from here. ("reordered" returned null
    // above: a same-strength verdict is exactly the claim a rules change makes
    // meaningless, and there is nothing else in it worth saying.)
    tone = "neutral"
    icon = ArrowLeftRight
    headline = "best setup changed"
    addGroup(groups, "gone", "No longer in your save", gone)
    addGroup(groups, "benched", "No longer used", benched)
    addGroup(groups, "entered", "Now uses", change.entered)
    note =
      "scoring rules changed since your last save — the old and new scores aren't comparable"
  } else if (status === "improved") {
    tone = "up"
    icon = TrendingUp
    const pct = percentLabel(before, after)
    // No usable baseline % means the build went from ~nothing to something.
    headline = pct ? `${pct} stronger` : "newly viable"
    addGroup(groups, "entered", "Now uses", change.entered)
    // A relic that left the save is news even on an improvement; one merely
    // displaced by something better is not.
    addGroup(groups, "gone", "No longer in your save", gone)
  } else if (status === "degraded") {
    tone = "down"
    icon = TrendingDown
    const pct = percentLabel(before, after)
    headline = pct ? `${pct} weaker` : "weaker"
    // The relics that actually left the save are the news; ones merely dropped
    // from the layout follow, and only with a note saying so — a permanent relic
    // reported as "lost" is exactly the confusion this replaces.
    addGroup(groups, "gone", "No longer in your save", gone)
    addGroup(groups, "benched", "No longer used", benched)
    addGroup(groups, "entered", "Now uses", change.entered)
    if (gone.length === 0 && benched.length > 0) {
      note = "still in your save — the best setup just moved on from them"
    }
  } else if (status === "broken_pin") {
    tone = "warn"
    icon = AlertTriangle
    headline = "a pinned relic left your save"
    addGroup(groups, "pin", "Pin lost", change.pinned_removed)
  } else {
    // potentially_affected — cheap relic-diff flag, no precise layout computed yet.
    tone = "neutral"
    icon = Sparkles
    const n = change.relevant_added ?? 0
    headline = n
      ? `${n} new relic${n === 1 ? "" : "s"} may help`
      : "new relics may help"
  }

  // Relics bought in Relic Rites are owned but not yet in the save file. The
  // change is real; the export is still owed, and the user has to be told which
  // half is which.
  const stagedCount = groups.reduce(
    (n, g) => n + g.relics.filter((r) => r.staged).length,
    0,
  )
  if (stagedCount > 0) {
    const owed = `${stagedCount === 1 ? "one relic is" : `${stagedCount} relics are`} from Relic Rites — export to write ${stagedCount === 1 ? "it" : "them"} to your save`
    note = note ? `${note}; ${owed}` : owed
  }

  return {
    tone,
    icon,
    headline,
    groups,
    note,
    rawScore,
    reliable: change.reliable !== false,
    textClass: TONE_TEXT[tone],
    boxClass: TONE_BOX[tone],
  }
}

/**
 * Everything that moved since the build's baseline.
 *
 * Falls back to the legacy single `cause` for snapshots written before the list
 * existed ("mixed" carried no detail, so it degrades to "relics" — the reading
 * every surface already gave it).
 */
export function changeCauses(change?: BuildChange | null): ChangeCause[] {
  if (!change) return []
  if (change.causes?.length) return change.causes as ChangeCause[]
  if (!change.cause) return []
  if (change.cause === "mixed") return ["relics"]
  return [change.cause as ChangeCause]
}

/**
 * Whether a change is news for the user rather than an echo of their own edit.
 *
 * Relics arriving (a newer save) and staged Relic Rites purchases both count:
 * a committed purchase is a real acquisition — the Murk is spent — and used to
 * be suppressed as if it were a hypothetical. Build edits and game-data bumps
 * re-baseline silently, as they always did.
 */
export function isChangeNews(change?: BuildChange | null): boolean {
  const causes = changeCauses(change)
  return causes.includes("relics") || causes.includes("staged")
}

/** "Crimson Whetblade, Stalwart Horn" — for tooltips/aria and compact rows. */
export function relicNames(relics: RelicRef[] | undefined): string {
  return (relics ?? [])
    .map((r) => r.name)
    .filter((n): n is string => !!n && n.length > 0)
    .join(", ")
}

/** One-line text form of a whole change, for `title`/aria on compact surfaces. */
export function changeSummaryText(d: ChangeDescription): string {
  const parts = d.groups.map(
    (g) => `${g.label.toLowerCase()}: ${relicNames(g.relics)}`,
  )
  return [d.headline, ...parts].join(" — ")
}

/** "387 → 478 pts" — raw optimizer score for a hover tooltip. */
export function rawScoreTooltip(
  rawScore: ChangeDescription["rawScore"],
): string | undefined {
  if (!rawScore) return undefined
  return `${rawScore.before} → ${rawScore.after} pts`
}
