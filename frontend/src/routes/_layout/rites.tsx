import { useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { Check, Coins, Package, Plus, Sparkles, Trash2, X } from "lucide-react"
import { type ReactNode, Suspense, useMemo, useRef, useState } from "react"

import { BuildsService, GameService, SavesService } from "@/client"
import { EmptyState } from "@/components/Common/EmptyState"
import { RELIC_CAP } from "@/components/inventory/types"
import {
  buildEffectMap,
  EffectList,
  RelicNameCell,
} from "@/components/RelicDisplay"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { isLoggedIn } from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"
import { toInlineBuild, useLocalBuilds } from "@/hooks/useLocalBuilds"
import {
  addMints,
  effectiveMurks,
  murkAdjustment,
  stagedFields,
  usePendingSlot,
} from "@/lib/pendingChanges"
import { getOriginalBackupFile } from "@/lib/saveBackup"
import { getSaveFile } from "@/lib/saveFile"
import { formatMurks } from "@/lib/sellValue"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/_layout/rites")({
  component: RitesPage,
  head: () => ({
    meta: [{ title: "Relic Rites - Nightreign Relic Planner" }],
  }),
})

const EMPTY = 4294967295
const TIER_NAMES: Record<number, string> = {
  1: "Delicate",
  2: "Polished",
  3: "Grand",
}
const PURCHASE_COLORS = ["Red", "Blue", "Yellow", "Green"]

// Buy prices are Murk, exact from regulation.bin ShopLineupParam (see RELIC_GENERATION_RE.md).
type Bucket = {
  key: string
  label: string
  is_deep: boolean
  version: string
  cost: number
}
const BUCKETS: Bucket[] = [
  {
    key: "n103",
    label: "Normal · Current",
    is_deep: false,
    version: "1.03",
    cost: 600,
  },
  {
    key: "d103",
    label: "Deep · Current",
    is_deep: true,
    version: "1.03",
    cost: 1800,
  },
  {
    key: "n102",
    label: "Normal · Legacy",
    is_deep: false,
    version: "1.02",
    cost: 600,
  },
  {
    key: "d102",
    label: "Deep · Legacy",
    is_deep: true,
    version: "1.02",
    cost: 1800,
  },
]

type StopMode = "fixed" | "budget" | "all_murk"

// Builds available to match against. The user opts in by selecting a subset (default
// none -> rules-only, no optimizer). Auth sends build_ids; anon sends the matching inline
// BuildDefinitions.
type BuildOption = { id: string; name: string }
type BuildsForm =
  | { kind: "auth"; options: BuildOption[] }
  | {
      kind: "anon"
      options: BuildOption[]
      inlineById: Record<string, Record<string, unknown>>
    }

type EffectOption = { id: number; name: string; isDebuff: boolean }

// A keep/sell rule for PURCHASED relics, built from inventory-style filters.
// Serialized to the backend Rule shape (see /saves/rites/plan). Effects are
// split into primary vs curse ids by is_debuff so the same picker feeds
// has_effect_ids / has_curse_ids.
type Rule = {
  id: string
  counts: number[] // 1|2|3
  colors: string[]
  effectIds: number[]
  curseIds: number[]
}

type PlanKeeper = {
  real_id: number
  item_id: number
  color: string
  tier: string
  is_deep: boolean
  name: string
  effects: number[]
  curses: number[]
  builds: string[]
  build_ranks?: number[] // best loadout rank per builds[] entry (1 = best; 0 = unknown)
  reason?: string // "inclusion" | "build" (backend rule support; optional)
}
type PlanResponse = {
  keepers: PlanKeeper[]
  generated: number
  kept: number
  duds: number
  murk_before: number // effective wallet: save Murk + staged mint delta
  murk_after: number
  murk_cost: number
  murk_refunded: number
  murk_delta: number // mint-side net (refunds − cost); staged-sell refunds excluded
  limited_by: string | null
  add_capacity: number // max relics one export can mint (ghost slots, pre-sells)
  storage_left: number // 1950 − effective owned (staged diff applied); fixed/budget also capped by ghost capacity
  pending_sold: number // staged sells the plan honored (any mode)
  pending_sold_refund: number // their total sell value (funded the run)
  rule_sold?: number // relics force-sold by an exclusion rule (optional)
}

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem("access_token")
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function planDetail(res: Response): Promise<string> {
  try {
    const d = (await res.json()).detail
    return typeof d === "string" ? d : (d?.message ?? "Request failed")
  } catch {
    return "Request failed"
  }
}

function toggleIn<T>(arr: T[], v: T): T[] {
  return arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]
}

function newRule(): Rule {
  return {
    id: crypto.randomUUID(),
    counts: [],
    colors: [],
    effectIds: [],
    curseIds: [],
  }
}

function ruleHasCondition(r: Rule): boolean {
  return (
    r.counts.length > 0 ||
    r.colors.length > 0 ||
    r.effectIds.length > 0 ||
    r.curseIds.length > 0
  )
}

function serializeRule(r: Rule): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  if (r.counts.length) out.effect_counts = r.counts
  if (r.colors.length) out.colors = r.colors
  if (r.effectIds.length) out.has_effect_ids = r.effectIds
  if (r.curseIds.length) out.has_curse_ids = r.curseIds
  return out
}

// --- Searchable effect/curse picker (self-contained, no popover dep) --------

function EffectPicker({
  options,
  selectedIds,
  onToggle,
  placeholder,
}: {
  options: EffectOption[]
  selectedIds: number[]
  onToggle: (id: number, isDebuff: boolean) => void
  placeholder: string
}) {
  const [q, setQ] = useState("")
  const matches = useMemo(() => {
    const query = q.trim().toLowerCase()
    if (!query) return []
    return options
      .filter((o) => o.name.toLowerCase().includes(query))
      .slice(0, 12)
  }, [q, options])

  return (
    <div className="space-y-1">
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-md border bg-background px-2 py-1 text-xs"
      />
      {matches.length > 0 && (
        <div className="max-h-40 overflow-auto rounded-md border text-xs">
          {matches.map((o) => (
            <button
              key={o.id}
              type="button"
              onClick={() => {
                onToggle(o.id, o.isDebuff)
                setQ("")
              }}
              className="flex w-full items-center justify-between px-2 py-1 text-left hover:bg-muted"
            >
              <span>{o.name}</span>
              {o.isDebuff && <span className="text-red-500">curse</span>}
            </button>
          ))}
        </div>
      )}
      {selectedIds.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {selectedIds.map((id) => {
            const o = options.find((x) => x.id === id)
            return (
              <Badge
                key={id}
                variant="secondary"
                className="cursor-pointer gap-1"
                onClick={() => onToggle(id, o?.isDebuff ?? false)}
              >
                {o?.name ?? `#${id}`}
                <X className="h-3 w-3" />
              </Badge>
            )
          })}
        </div>
      )}
    </div>
  )
}

// --- Inclusion / exclusion rule builder ------------------------------------

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-md border px-2 py-0.5 text-xs",
        active ? "bg-primary text-primary-foreground" : "bg-background",
      )}
    >
      {children}
    </button>
  )
}

function RuleBuilder({
  title,
  hint,
  rules,
  setRules,
  options,
}: {
  title: string
  hint: string
  rules: Rule[]
  setRules: (r: Rule[]) => void
  options: EffectOption[]
}) {
  const patch = (id: string, p: Partial<Rule>) =>
    setRules(rules.map((r) => (r.id === id ? { ...r, ...p } : r)))

  return (
    <div className="space-y-2 rounded-lg border p-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <span className="text-sm font-medium">{title}</span>
          <p className="text-xs text-muted-foreground">{hint}</p>
        </div>
        <Button
          size="sm"
          variant="outline"
          className="gap-1"
          onClick={() => setRules([...rules, newRule()])}
        >
          <Plus className="h-3.5 w-3.5" /> Rule
        </Button>
      </div>

      {rules.length === 0 && (
        <p className="text-xs text-muted-foreground">
          No rules — nothing forced.
        </p>
      )}

      {rules.map((r) => (
        <div key={r.id} className="space-y-2 rounded-md border p-2">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-xs text-muted-foreground">Properties</span>
            {[1, 2, 3].map((n) => (
              <Chip
                key={n}
                active={r.counts.includes(n)}
                onClick={() => patch(r.id, { counts: toggleIn(r.counts, n) })}
              >
                {TIER_NAMES[n]} ({n})
              </Chip>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-xs text-muted-foreground">Color</span>
            {PURCHASE_COLORS.map((c) => (
              <Chip
                key={c}
                active={r.colors.includes(c)}
                onClick={() => patch(r.id, { colors: toggleIn(r.colors, c) })}
              >
                {c}
              </Chip>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="ghost"
              className="ml-auto h-7 gap-1 text-muted-foreground"
              onClick={() => setRules(rules.filter((x) => x.id !== r.id))}
            >
              <Trash2 className="h-3.5 w-3.5" /> Remove
            </Button>
          </div>
          <EffectPicker
            options={options}
            selectedIds={[...r.effectIds, ...r.curseIds]}
            placeholder="Contains effect or curse…"
            onToggle={(id, isDebuff) =>
              isDebuff
                ? patch(r.id, { curseIds: toggleIn(r.curseIds, id) })
                : patch(r.id, { effectIds: toggleIn(r.effectIds, id) })
            }
          />
        </div>
      ))}
    </div>
  )
}

// --- Local sell-value mirror (matches nrplanner.writer.sell_value; deep x2) --

function sellValueLocal(effects: number[], isDeep: boolean): number {
  const count = Math.min(
    Math.max(
      effects.filter((e) => e !== EMPTY && e !== 0 && e !== -1).length,
      1,
    ),
    3,
  )
  const base = count === 3 ? 550 : count === 2 ? 350 : 150
  return base * (isDeep ? 2 : 1)
}

// --- Find Keepers progress stepper ------------------------------------------

// Mirrors the SSE progress events from /saves/rites/plan/stream.
type ProgressEvt = {
  phase: string
  current: number
  total: number
  message: string
  name: string | null // current build's name, on per-build phases only
}

// Ordered to match the backend plan phases (saves.py _rites_plan):
// generating → matching → finalizing. Only purchased relics are ever touched —
// the plan never scans or sells relics already in the save.
const PROGRESS_STEPS = [
  {
    key: "generating",
    label: "Roll purchases",
    sub: "Reading the save & buying at the game's exact odds",
    perBuild: false,
  },
  { key: "matching", label: "Match new relics against builds", perBuild: true },
  { key: "finalizing", label: "Tally Murk & finalize", perBuild: false },
] as const

function RitesProgress({
  progress,
  seen,
}: {
  progress: ProgressEvt | null
  seen: Record<string, ProgressEvt>
}) {
  const rawIdx = PROGRESS_STEPS.findIndex((s) => s.key === progress?.phase)
  const activeIdx = rawIdx === -1 ? 0 : rawIdx

  return (
    <ol className="rounded-md border bg-muted/30 p-3">
      {PROGRESS_STEPS.map((s, i) => {
        const state =
          i < activeIdx ? "done" : i === activeIdx ? "active" : "pending"
        // Live event for the active step; last-seen event for finished steps
        // (so "Match" keeps its 55/55 once "Cull" starts).
        const evt = s.key === progress?.phase ? progress : seen[s.key]
        const pct =
          evt && evt.total > 0 ? Math.round((evt.current / evt.total) * 100) : 0
        const name = state === "active" ? (evt?.name ?? null) : null

        return (
          <li key={s.key} className="relative flex gap-2.5 pb-3 last:pb-0">
            {i < PROGRESS_STEPS.length - 1 && (
              <span
                aria-hidden="true"
                className="absolute bottom-0 left-[11px] top-7 w-px bg-border"
              />
            )}
            {state === "done" ? (
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-green-600/15 text-green-600 dark:text-green-500">
                <Check className="h-3.5 w-3.5" />
              </span>
            ) : state === "active" ? (
              <span className="relative flex h-6 w-6 shrink-0 items-center justify-center">
                <span className="absolute inset-0.5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
              </span>
            ) : (
              <span className="flex h-6 w-6 shrink-0 items-center justify-center">
                <span className="h-2 w-2 rounded-full bg-muted-foreground/30" />
              </span>
            )}
            <div className="min-w-0 flex-1 pt-0.5">
              <div className="flex items-baseline justify-between gap-2">
                <span
                  className={cn(
                    "text-sm",
                    state === "pending" && "text-muted-foreground/60",
                    state === "active" && "font-medium",
                    state === "done" && "text-muted-foreground",
                  )}
                >
                  {s.label}
                </span>
                {s.perBuild && evt && evt.total > 0 && state !== "pending" && (
                  <span className="text-xs tabular-nums text-muted-foreground">
                    {evt.current}/{evt.total}
                  </span>
                )}
              </div>
              {state === "active" && s.perBuild && evt && evt.total > 0 && (
                <>
                  <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-primary transition-[width] duration-300 ease-out"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  {name && (
                    <div className="mt-1 truncate text-xs text-muted-foreground">
                      {name}
                    </div>
                  )}
                </>
              )}
              {state === "active" && !s.perBuild && "sub" in s && (
                <div className="mt-0.5 text-xs text-muted-foreground">
                  {s.sub}
                </div>
              )}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

// --- The tool (shared by authenticated + anonymous) ------------------------

function RitesTool({
  slotIndex,
  murks,
  buildsForm,
  effectMap,
  effectOptions,
}: {
  slotIndex: number
  murks: number | null
  buildsForm: BuildsForm
  effectMap: Map<number, string>
  effectOptions: EffectOption[]
}) {
  const { showErrorToast, showSuccessToast } = useCustomToast()
  // Staged diff for this slot. Murk is emulated LIVE against it: the wallet
  // shown, budgeted, and planned with is the save's Murk plus the staged
  // adjustment (rites spend + staged-sell refunds), so staged spending
  // persists across refreshes and re-runs until export.
  const pending = usePendingSlot(slotIndex)
  const pendingSells = pending.sells
  const staged = murkAdjustment(pending)
  const effMurks = effectiveMurks(murks, pending)
  const [stopMode, setStopMode] = useState<StopMode>("fixed")
  const [budget, setBudget] = useState<number>(
    Math.min(effMurks ?? 100000, 100000),
  )
  const [qty, setQty] = useState<Record<string, number>>({
    n103: 50,
    d103: 0,
    n102: 0,
    d102: 0,
  })
  const [inclusion, setInclusion] = useState<Rule[]>([])
  const [exclusion, setExclusion] = useState<Rule[]>([])
  const [busy, setBusy] = useState(false)
  const [plan, setPlan] = useState<PlanResponse | null>(null)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [selectedBuilds, setSelectedBuilds] = useState<Set<string>>(new Set())
  const [progress, setProgress] = useState<ProgressEvt | null>(null)
  // How many builds the in-flight run was started with (selection can change
  // while it runs); >0 switches the busy UI to the multi-step indicator.
  const [runBuildCount, setRunBuildCount] = useState(0)
  // Stop mode the displayed plan was run with (the selector can change after).
  const [planStop, setPlanStop] = useState<StopMode>("fixed")
  // Build-match depth: keep a purchase only if it lands in one of a build's
  // top-N ranked loadouts. 10 = the optimizer page's full list (widest).
  const [topN, setTopN] = useState(10)
  // Last event per phase, so completed steps keep their final N/N count.
  const seenRef = useRef<Record<string, ProgressEvt>>({})
  const abortRef = useRef<AbortController | null>(null)

  const activeBuckets = BUCKETS.filter((b) => (qty[b.key] ?? 0) > 0)
  const fixedCost = activeBuckets.reduce(
    (n, b) => n + (qty[b.key] ?? 0) * b.cost,
    0,
  )
  const overspend =
    stopMode === "fixed" && effMurks != null && fixedCost > effMurks

  async function findKeepers() {
    const file = getSaveFile() ?? (await getOriginalBackupFile())
    if (!file) {
      showErrorToast(
        "Re-select your save file (open the Changes panel) so Rites can read it.",
      )
      return
    }
    if (!activeBuckets.length) {
      showErrorToast("Choose at least one relic type to buy.")
      return
    }
    const buckets = activeBuckets.map((b) => ({
      is_deep: b.is_deep,
      version: b.version,
      ...(stopMode === "fixed" ? { quantity: qty[b.key] } : {}),
    }))
    const selIds = [...selectedBuilds]
    const form = new FormData()
    form.append("file", file, file.name || "save.sl2")
    form.append("slot_index", String(slotIndex))
    if (buildsForm.kind === "auth")
      form.append("build_ids", JSON.stringify(selIds))
    else
      form.append(
        "builds",
        JSON.stringify(
          selIds.map((id) => buildsForm.inlineById[id]).filter(Boolean),
        ),
      )
    form.append("buckets", JSON.stringify(buckets))
    form.append("stop_mode", stopMode)
    form.append("top_n", String(topN))
    if (stopMode === "budget") form.append("budget", String(budget))
    // The staged diff rides along so the plan runs against the LIVE app
    // state in every mode: sells are already gone (freed storage, refunds
    // spendable), staged mints are already owned (no double-mint), the
    // batch's net Murk delta spends down the wallet (no double-spend), and
    // staged bookmark toggles drive the protected-sell gate (un-bookmark +
    // trash in one session must not 422).
    if (pendingSells.length)
      form.append("sold_handles", JSON.stringify(pendingSells))
    const stagedMints = stagedFields(pending).staged_mints
    if (stagedMints.length)
      form.append("staged_mints", JSON.stringify(stagedMints))
    if (pending.murkDelta !== 0)
      form.append("staged_murk_delta", String(pending.murkDelta))
    if (Object.keys(pending.favorites).length)
      form.append("staged_favorites", JSON.stringify(pending.favorites))
    const inc = inclusion.filter(ruleHasCondition).map(serializeRule)
    const exc = exclusion.filter(ruleHasCondition).map(serializeRule)
    if (inc.length) form.append("inclusion_rules", JSON.stringify(inc))
    if (exc.length) form.append("exclusion_rules", JSON.stringify(exc))

    const ctrl = new AbortController()
    abortRef.current = ctrl
    setBusy(true)
    setPlan(null)
    setRunBuildCount(selIds.length)
    seenRef.current = {}
    setProgress(null)
    try {
      // Streaming (SSE): show progress while the plan runs, so many builds don't
      // look like a hang. Falls through to a single result event at the end.
      const res = await fetch("/api/v1/saves/rites/plan/stream", {
        method: "POST",
        headers: authHeaders(),
        body: form,
        signal: ctrl.signal,
      })
      if (!res.ok || !res.body) throw new Error(await planDetail(res))
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ""
      let result: PlanResponse | null = null
      let streamErr: string | null = null
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const chunks = buf.split("\n\n")
        buf = chunks.pop() ?? ""
        for (const chunk of chunks) {
          const dataLine = chunk.split("\n").find((l) => l.startsWith("data:"))
          if (!dataLine) continue
          const evt = JSON.parse(dataLine.slice(5).trim())
          if (evt.type === "progress") {
            const p: ProgressEvt = {
              phase: evt.phase ?? "",
              current: evt.current ?? 0,
              total: evt.total ?? 0,
              message: evt.message || "Working…",
              name: evt.name ?? null,
            }
            seenRef.current[p.phase] = p
            setProgress(p)
          } else if (evt.type === "result") result = evt.data as PlanResponse
          else if (evt.type === "error") streamErr = evt.detail
        }
      }
      if (streamErr) throw new Error(streamErr)
      if (!result) throw new Error("No plan was returned.")
      setPlan(result)
      setPlanStop(stopMode)
      setSelected(new Set(result.keepers.map((_, i) => i)))
    } catch (err) {
      if ((err as Error)?.name !== "AbortError")
        showErrorToast(
          err instanceof Error ? err.message : "Failed to find keepers.",
        )
    } finally {
      abortRef.current = null
      setBusy(false)
      setProgress(null)
    }
  }

  function cancelFind() {
    abortRef.current?.abort()
  }

  function stageKeepers() {
    if (!plan) return
    const keep = plan.keepers.filter((_, i) => selected.has(i))
    if (!keep.length) {
      showErrorToast("Select at least one keeper to add.")
      return
    }
    // The plan's murk_delta assumes ALL keepers are kept. A deselected keeper is
    // instead sold, so refund its sell value on top (faithful to buy-then-sell).
    // Staged-sell refunds are NOT in murk_delta — those sells credit themselves
    // through the export's sell step.
    const dropped = plan.keepers.filter((_, i) => !selected.has(i))
    const extraRefund = dropped.reduce(
      (n, k) => n + sellValueLocal(k.effects, k.is_deep),
      0,
    )
    addMints(
      slotIndex,
      keep.map((k) => ({
        real_id: k.real_id,
        item_id: k.item_id,
        effects: k.effects,
        curses: k.curses,
        name: k.name,
        color: k.color,
        tier: k.tier,
        isDeep: k.is_deep,
        // Odds are exact (relic_lots.json, derived from regulation.bin — see RE doc).
        oddsSource: "exact",
        builds: k.builds,
      })),
      plan.murk_delta + extraRefund,
    )
    showSuccessToast(
      `Staged ${keep.length} keeper(s). Review and export from the Changes panel.`,
    )
    setPlan(null)
    setSelected(new Set())
  }

  return (
    <div className="space-y-6">
      {/* Balances (live: save Murk with the staged diff applied) */}
      <div className="flex flex-wrap gap-4 text-sm">
        <span className="inline-flex items-center gap-1.5">
          <Coins className="h-4 w-4 text-amber-500" />
          <strong>{effMurks != null ? formatMurks(effMurks) : "—"}</strong> Murk
          {staged !== 0 && murks != null && (
            <span className="text-xs text-muted-foreground">
              (save {formatMurks(murks)} {staged < 0 ? "−" : "+"}{" "}
              {formatMurks(Math.abs(staged))} staged)
            </span>
          )}
        </span>
        {plan && planStop === "all_murk" ? (
          // storage_left = 1950 − owned after staged sells, so occupancy after
          // staging the selected keepers is (1950 − storage_left) + selected.
          <span className="text-muted-foreground">
            Storage:{" "}
            <strong>
              {(RELIC_CAP - plan.storage_left + selected.size).toLocaleString()}
            </strong>{" "}
            / {RELIC_CAP.toLocaleString()}
            {plan.pending_sold > 0 &&
              ` (after ${plan.pending_sold} staged sell${plan.pending_sold === 1 ? "" : "s"})`}
          </span>
        ) : (
          <span className="text-muted-foreground">
            Storage cap: {RELIC_CAP}
          </span>
        )}
      </div>

      {/* Match against builds (opt-in + scoped — avoids optimizing every build) */}
      <div className="space-y-2 rounded-lg border p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-sm font-medium">Match against builds</span>
          <span className="text-xs text-muted-foreground">
            {selectedBuilds.size === 0
              ? "None — keepers decided by your rules only (instant)"
              : `${selectedBuilds.size} selected — runs the optimizer per build (slower; progress shown)`}
          </span>
        </div>
        {buildsForm.options.length > 1 && (
          <div className="flex items-center gap-3 text-xs">
            <button
              type="button"
              onClick={() =>
                setSelectedBuilds(new Set(buildsForm.options.map((b) => b.id)))
              }
              className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
            >
              Select all ({buildsForm.options.length})
            </button>
            <button
              type="button"
              onClick={() => setSelectedBuilds(new Set())}
              disabled={selectedBuilds.size === 0}
              className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline disabled:opacity-40 disabled:no-underline"
            >
              Select none
            </button>
          </div>
        )}
        {buildsForm.options.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No builds yet —{" "}
            <Link to="/builds" className="underline">
              create one
            </Link>{" "}
            to keep relics your builds would actually use.
          </p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {buildsForm.options.map((b) => {
              const on = selectedBuilds.has(b.id)
              return (
                <button
                  key={b.id}
                  type="button"
                  onClick={() =>
                    setSelectedBuilds((s) => {
                      const n = new Set(s)
                      if (n.has(b.id)) n.delete(b.id)
                      else n.add(b.id)
                      return n
                    })
                  }
                  className={cn(
                    "rounded-full border px-2.5 py-1 text-xs transition-colors",
                    on
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-muted-foreground/30 text-muted-foreground hover:border-foreground",
                  )}
                >
                  {b.name}
                </button>
              )
            })}
          </div>
        )}
        {selectedBuilds.size > 0 && (
          <div className="flex flex-wrap items-center gap-2 pt-1 text-xs">
            <label htmlFor="rites-match-depth" className="font-medium">
              Match depth
            </label>
            <select
              id="rites-match-depth"
              value={topN}
              onChange={(e) => setTopN(Number(e.target.value))}
              className="rounded-md border bg-background px-2 py-1"
            >
              {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => (
                <option key={n} value={n}>
                  Top {n}
                </option>
              ))}
            </select>
            <span className="text-muted-foreground">
              Keep a purchase only if it earns a spot in one of a build's top{" "}
              {topN} ranked loadout{topN === 1 ? "" : "s"} — lower is stricter.
            </span>
          </div>
        )}
        {selectedBuilds.size > 10 && (
          <p className="text-xs text-amber-600 dark:text-amber-500">
            {selectedBuilds.size} builds selected — this runs the optimizer that
            many times and can take a while. Progress is shown as it works.
          </p>
        )}
      </div>

      {/* Buckets */}
      <div className="space-y-4 rounded-lg border p-4">
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm font-medium">Stop when</span>
          <Select
            value={stopMode}
            onValueChange={(v) => {
              const mode = v as StopMode
              if (mode !== "fixed") {
                // Budget / all-Murk spend from one shared pool — collapse to a
                // single flatstone type so the allocation is unambiguous.
                setQty((q) => {
                  const active =
                    BUCKETS.find((b) => (q[b.key] ?? 0) > 0) ?? BUCKETS[0]
                  return { [active.key]: 1 }
                })
              }
              setStopMode(mode)
            }}
          >
            <SelectTrigger className="w-56">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="fixed">A fixed quantity is bought</SelectItem>
              <SelectItem value="budget">A Murk budget is spent</SelectItem>
              <SelectItem value="all_murk">All Murk is spent</SelectItem>
            </SelectContent>
          </Select>
          {stopMode === "budget" && (
            <input
              type="number"
              min={0}
              max={effMurks ?? undefined}
              value={budget}
              onChange={(e) => setBudget(Number(e.target.value))}
              className="w-32 rounded-md border bg-background px-2 py-1 text-sm"
              aria-label="Murk budget"
            />
          )}
        </div>

        {stopMode !== "fixed" && (
          <p className="text-xs text-muted-foreground">
            Pick one flatstone type — your{" "}
            {stopMode === "budget" ? "budget" : "Murk"} is spent entirely on it.
          </p>
        )}

        <div className="grid gap-2 sm:grid-cols-2">
          {BUCKETS.map((b) => (
            <div
              key={b.key}
              className="flex items-center justify-between gap-2 rounded-md border px-3 py-2 text-sm"
            >
              <span>
                {b.label}
                <span className="ml-1 text-xs text-muted-foreground">
                  ({formatMurks(b.cost)} Murk)
                </span>
              </span>
              {stopMode === "fixed" ? (
                <input
                  type="number"
                  min={0}
                  value={qty[b.key] ?? 0}
                  onChange={(e) =>
                    setQty((q) => ({
                      ...q,
                      [b.key]: Math.max(0, Number(e.target.value)),
                    }))
                  }
                  className="w-20 rounded-md border bg-background px-2 py-1"
                  aria-label={`${b.label} quantity`}
                />
              ) : (
                <input
                  type="radio"
                  name="rites-bucket"
                  checked={(qty[b.key] ?? 0) > 0}
                  onChange={() => setQty({ [b.key]: 1 })}
                  className="h-4 w-4"
                  aria-label={`Buy ${b.label}`}
                />
              )}
            </div>
          ))}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-sm text-muted-foreground">
            {stopMode === "fixed"
              ? `Up to ${formatMurks(fixedCost)} Murk`
              : stopMode === "budget"
                ? `Spend up to ${formatMurks(budget)} Murk`
                : "Spend all your Murk"}
            {overspend && (
              <span className="ml-2 text-red-500">— more than you have</span>
            )}
          </span>
          <Button
            onClick={findKeepers}
            disabled={busy || overspend}
            className="gap-1.5"
          >
            <Sparkles className="h-4 w-4" />
            {busy ? "Working…" : "Find keepers"}
          </Button>
          {busy && (
            <Button variant="ghost" size="sm" onClick={cancelFind}>
              Cancel
            </Button>
          )}
        </div>
        {busy &&
          (runBuildCount > 0 ? (
            <RitesProgress progress={progress} seen={seenRef.current} />
          ) : (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
              {progress?.message || "Rolling…"}
            </div>
          ))}
      </div>

      {/* Cull rules */}
      <div className="grid gap-3 md:grid-cols-2">
        <RuleBuilder
          title="Always keep"
          hint="A relic matching any rule is kept, even if no build uses it."
          rules={inclusion}
          setRules={setInclusion}
          options={effectOptions}
        />
        <RuleBuilder
          title="Always sell"
          hint="A relic matching any rule is sold. Inclusion wins over exclusion."
          rules={exclusion}
          setRules={setExclusion}
          options={effectOptions}
        />
      </div>

      {/* Results */}
      {plan && (
        <div className="space-y-4 rounded-lg border p-4">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
            <span>
              Rolled <strong>{plan.generated}</strong> · kept{" "}
              <strong className="text-green-600 dark:text-green-500">
                {plan.kept}
              </strong>{" "}
              · sold {plan.duds}
              {plan.rule_sold ? ` (${plan.rule_sold} by rule)` : ""}
            </span>
            <span>
              Murk: {formatMurks(plan.murk_before)} →{" "}
              <strong>{formatMurks(plan.murk_after)}</strong>{" "}
              <span className="text-muted-foreground">
                (−{formatMurks(plan.murk_cost)} +
                {formatMurks(plan.murk_refunded)}
                {plan.pending_sold_refund > 0 &&
                  ` +${formatMurks(plan.pending_sold_refund)} staged sells`}
                )
              </span>
            </span>
            {plan.limited_by && (
              <Badge variant="secondary">limited by {plan.limited_by}</Badge>
            )}
          </div>
          {plan.limited_by === "storage" && (
            <p className="text-xs text-muted-foreground">
              Storage is full — trash relics from the Inventory and re-run.
              Staged sells free space for this simulation.
            </p>
          )}
          {plan.kept > plan.add_capacity && (
            <p className="text-xs text-amber-600 dark:text-amber-500">
              Your next export can mint {plan.add_capacity} more relic(s) — the
              server already counts staged sells (each frees a slot) and staged
              purchases (each claims one). Stage more sells or export in
              batches.
            </p>
          )}

          {plan.keepers.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No keepers — none of the rolled relics earned a spot in a selected
              build's top loadouts (at the chosen match depth) or matched an
              "always keep" rule. Try a larger batch, a wider match depth, or a
              rule.
            </p>
          ) : (
            // Same presentation as the Inventory table (RelicNameCell +
            // EffectList), with a "Kept for" column naming each keeper's builds.
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-10">
                    <Checkbox
                      checked={selected.size === plan.keepers.length}
                      onCheckedChange={() =>
                        setSelected((s) =>
                          s.size === plan.keepers.length
                            ? new Set()
                            : new Set(plan.keepers.map((_, i) => i)),
                        )
                      }
                      aria-label="Select all keepers"
                    />
                  </TableHead>
                  <TableHead>Relic</TableHead>
                  <TableHead>Effects</TableHead>
                  <TableHead className="w-44">Kept for</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {plan.keepers.map((k, i) => {
                  const effectsCell = EffectList({
                    effectIds: k.effects,
                    isCurse: false,
                    effectMap,
                  })
                  const cursesCell = EffectList({
                    effectIds: k.curses,
                    isCurse: true,
                    effectMap,
                  })
                  return (
                    <TableRow
                      key={`${k.item_id}-${i}`}
                      data-state={selected.has(i) ? "selected" : undefined}
                    >
                      <TableCell>
                        <Checkbox
                          checked={selected.has(i)}
                          onCheckedChange={() =>
                            setSelected((s) => {
                              const n = new Set(s)
                              n.has(i) ? n.delete(i) : n.add(i)
                              return n
                            })
                          }
                          aria-label={`Select ${k.name}`}
                        />
                      </TableCell>
                      <TableCell className="min-w-[180px]">
                        <RelicNameCell
                          name={k.name}
                          color={k.color}
                          tier={TIER_NAMES[Number(k.tier)] ?? String(k.tier)}
                          isDeep={k.is_deep}
                        />
                      </TableCell>
                      <TableCell>
                        {effectsCell || cursesCell ? (
                          <div className="flex flex-col gap-1.5">
                            {effectsCell}
                            {cursesCell}
                          </div>
                        ) : (
                          <span className="text-xs text-muted-foreground italic">
                            —
                          </span>
                        )}
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-wrap gap-1">
                          {k.reason === "inclusion" && (
                            <Badge variant="default" className="text-[10px]">
                              kept by rule
                            </Badge>
                          )}
                          {k.reason === "kept" && (
                            <Badge variant="secondary" className="text-[10px]">
                              no build filter
                            </Badge>
                          )}
                          {k.builds.map((b, bi) => {
                            const rank = k.build_ranks?.[bi] ?? 0
                            const isBest = rank === 1
                            return (
                              <Badge
                                key={b}
                                variant={isBest ? "default" : "outline"}
                                className={cn(
                                  "text-[10px]",
                                  isBest &&
                                    "border-transparent bg-amber-500 text-amber-950 hover:bg-amber-500",
                                )}
                                title={
                                  isBest
                                    ? `Lands in ${b}'s single best loadout`
                                    : rank > 0
                                      ? `Lands in ${b}'s #${rank} ranked loadout`
                                      : undefined
                                }
                              >
                                {b}
                                {rank > 0 && (
                                  <span
                                    className={
                                      isBest ? "font-bold" : "opacity-70"
                                    }
                                  >
                                    {" "}
                                    #{rank}
                                  </span>
                                )}
                              </Badge>
                            )
                          })}
                        </div>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          )}

          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button
              onClick={stageKeepers}
              disabled={selected.size === 0}
              className="gap-1.5"
            >
              Add {selected.size} keeper(s) to Changes
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

// --- Shared: profile picker + effect options -------------------------------

function useEffectOptions() {
  const { data: effectsData } = useSuspenseQuery({
    queryKey: ["game", "effects"],
    queryFn: () => GameService.getEffects(),
    staleTime: Number.POSITIVE_INFINITY,
  })
  const effectMap = useMemo(
    () => buildEffectMap((effectsData ?? []) as unknown[]),
    [effectsData],
  )
  const effectOptions = useMemo<EffectOption[]>(
    () =>
      ((effectsData ?? []) as Array<Record<string, unknown>>).map((e) => ({
        id: e.id as number,
        name: (e.name as string) ?? `Effect ${e.id}`,
        isDebuff: Boolean(e.is_debuff),
      })),
    [effectsData],
  )
  return { effectMap, effectOptions }
}

// --- Authenticated body (DB profiles + saved builds) -----------------------

function AuthRitesBody() {
  const { data: profiles } = useSuspenseQuery({
    queryKey: ["profiles"],
    queryFn: () => SavesService.listProfiles(),
    staleTime: 5 * 60 * 1000,
  })
  const { data: builds } = useSuspenseQuery({
    queryKey: ["builds"],
    queryFn: () => BuildsService.listBuilds(),
    staleTime: 5 * 60 * 1000,
  })
  const { effectMap, effectOptions } = useEffectOptions()
  const [selectedId, setSelectedId] = useState<string | null>(
    profiles.data?.[0]?.id ?? null,
  )

  if (!profiles.data?.length) return <NoSave />
  const buildOptions = (builds.data ?? []).map((b) => ({
    id: b.id,
    name: b.name,
  }))
  if (!buildOptions.length) return <NoBuilds />

  const selected =
    profiles.data.find((p) => p.id === selectedId) ?? profiles.data[0]

  return (
    <div className="space-y-4">
      {profiles.data.length > 1 && (
        <Select value={selected.id} onValueChange={setSelectedId}>
          <SelectTrigger className="w-56">
            <SelectValue placeholder="Select profile" />
          </SelectTrigger>
          <SelectContent>
            {profiles.data.map((c) => (
              <SelectItem key={c.id} value={c.id}>
                {c.name} (Slot {c.slot_index})
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      <RitesTool
        key={selected.id}
        slotIndex={selected.slot_index}
        murks={selected.murks ?? 0}
        buildsForm={{ kind: "auth", options: buildOptions }}
        effectMap={effectMap}
        effectOptions={effectOptions}
      />
    </div>
  )
}

// --- Anonymous body (session profiles + local builds, inline) --------------

interface SessionProfile {
  slot_index: number
  name: string
  murks?: number
}

function AnonRitesBody() {
  const { builds } = useLocalBuilds()
  const { effectMap, effectOptions } = useEffectOptions()

  const allProfiles: SessionProfile[] = useMemo(() => {
    try {
      return JSON.parse(sessionStorage.getItem("parsedProfiles") ?? "[]")
    } catch {
      return []
    }
  }, [])

  const [slot, setSlot] = useState<number | null>(
    allProfiles[0]?.slot_index ?? null,
  )

  if (!allProfiles.length) return <NoSave />
  if (!builds.length) return <NoBuilds />

  const selected =
    allProfiles.find((p) => p.slot_index === slot) ?? allProfiles[0]
  const buildOptions = builds.map((b) => ({ id: b.id, name: b.name }))
  const inlineById = Object.fromEntries(
    builds.map((b) => [b.id, toInlineBuild(b)]),
  )

  return (
    <div className="space-y-4">
      <p className="rounded-md border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
        Session mode — using your uploaded save + local builds.{" "}
        <Link to="/login" className="underline">
          Sign in
        </Link>{" "}
        to persist across devices.
      </p>
      {allProfiles.length > 1 && (
        <Select
          value={String(selected.slot_index)}
          onValueChange={(v) => setSlot(Number(v))}
        >
          <SelectTrigger className="w-56">
            <SelectValue placeholder="Select profile" />
          </SelectTrigger>
          <SelectContent>
            {allProfiles.map((p) => (
              <SelectItem key={p.slot_index} value={String(p.slot_index)}>
                {p.name} (Slot {p.slot_index})
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      <RitesTool
        key={selected.slot_index}
        slotIndex={selected.slot_index}
        murks={selected.murks ?? null}
        buildsForm={{ kind: "anon", options: buildOptions, inlineById }}
        effectMap={effectMap}
        effectOptions={effectOptions}
      />
    </div>
  )
}

// --- Empty states ----------------------------------------------------------

function NoSave() {
  return (
    <EmptyState
      icon={Package}
      title="No save loaded"
      action={
        <Button asChild size="sm">
          <Link to="/upload">Upload a save file</Link>
        </Button>
      }
    >
      Import your .sl2 to use Relic Rites.
    </EmptyState>
  )
}

function NoBuilds() {
  return (
    <EmptyState
      icon={Sparkles}
      title="No builds yet"
      action={
        <Button asChild size="sm">
          <Link to="/builds">Create a build</Link>
        </Button>
      }
    >
      Relic Rites keeps relics your builds would use — define at least one build
      first (or add "always keep" rules).
    </EmptyState>
  )
}

function RitesPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Relic Rites</h1>
        <p className="mt-1 text-muted-foreground">
          Bulk-buy relics the way the game does, keep only the ones your builds
          use (plus your own keep/sell rules), and sell the rest for Murk — the
          whole grind, in one click. Nothing changes until you export from the
          Changes panel.
        </p>
      </div>
      <Suspense fallback={<Skeleton className="h-48 w-full" />}>
        {isLoggedIn() ? <AuthRitesBody /> : <AnonRitesBody />}
      </Suspense>
    </div>
  )
}
