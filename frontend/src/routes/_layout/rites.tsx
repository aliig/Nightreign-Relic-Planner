import { useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import {
  Check,
  ChevronRight,
  Coins,
  Package,
  Plus,
  Sparkles,
  Trash2,
  X,
} from "lucide-react"
import {
  type ReactNode,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { toast } from "sonner"

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
import { CHARACTER_NAMES } from "@/lib/constants"
import {
  appendRitesBatch,
  effectiveMurks,
  type MintSpec,
  murkAdjustment,
  nextRollEpoch,
  type RitesBatch,
  type RitesBatchSpec,
  readSlot,
  removeMint,
  removeRitesBatch,
  stagedFields,
  usePendingSlot,
} from "@/lib/pendingChanges"
import { getOriginalBackupFile } from "@/lib/saveBackup"
import { getSaveFile } from "@/lib/saveFile"
import { effectCountOf, formatMurks, sellValue } from "@/lib/sellValue"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/_layout/rites")({
  component: RitesPage,
  head: () => ({
    meta: [{ title: "Relic Rites - Nightreign Relic Planner" }],
  }),
})

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

type StopMode = "fixed" | "budget" | "murk_target" | "all_murk"

// Builds available to match against. The user opts in by selecting a subset (default
// none -> rules-only, no optimizer). Auth sends build_ids; anon sends the matching inline
// BuildDefinitions.
type BuildOption = { id: string; name: string; character: string }

/**
 * Build names are free text and routinely collide (the same "Katanas" saved
 * for two Nightfarers), which left the selection chips — and the keeper tags
 * they turn into — impossible to tell apart. Qualify only the ones that
 * actually clash, so unique names stay short. The server does the same for
 * the names it tags keepers with (_disambiguate_names).
 */
function labelBuilds(
  builds: Array<{ id: string; name: string; character?: string }>,
): BuildOption[] {
  const counts = new Map<string, number>()
  for (const b of builds) counts.set(b.name, (counts.get(b.name) ?? 0) + 1)
  const seen = new Map<string, number>()
  return builds.map((b) => {
    const character = b.character ?? ""
    if ((counts.get(b.name) ?? 0) < 2 || !b.character)
      return { id: b.id, name: b.name, character }
    const qualified = `${b.name} (${b.character})`
    const n = (seen.get(qualified) ?? 0) + 1
    seen.set(qualified, n)
    return {
      id: b.id,
      name: n === 1 ? qualified : `${qualified} ${n}`,
      character,
    }
  })
}

/**
 * Group the selection chips by Nightfarer so a long build list reads as one
 * block per character instead of an undifferentiated wrap of names. Roster
 * order (CHARACTER_NAMES) first, then any unrecognized character name, then
 * builds with no character at all; alphabetical by build name within a group.
 */
function groupBuildsByCharacter(
  options: BuildOption[],
): Array<{ character: string; builds: BuildOption[] }> {
  const byCharacter = new Map<string, BuildOption[]>()
  for (const b of options) {
    const list = byCharacter.get(b.character)
    if (list) list.push(b)
    else byCharacter.set(b.character, [b])
  }
  const rank = (character: string) => {
    if (!character) return CHARACTER_NAMES.length + 1
    const i = CHARACTER_NAMES.indexOf(character)
    return i === -1 ? CHARACTER_NAMES.length : i
  }
  return [...byCharacter.entries()]
    .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
    .map(([character, builds]) => ({
      character,
      builds: [...builds].sort((a, b) => a.name.localeCompare(b.name)),
    }))
}
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
  murk_before: number // wallet the plan ran from: raw save Murk (the staged batch never rides — a run replaces it)
  murk_after: number
  murk_cost: number
  murk_refunded: number
  murk_delta: number // mint-side net (refunds − cost); staged-sell refunds excluded
  limited_by: string | null
  add_capacity: number // max relics one export can mint (ghost + mintable free slots, pre-sells)
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

// --- Batch presentation ------------------------------------------------------

/** When a batch was bought: "just now" / "14 min ago" / a clock time. */
function formatWhen(at: number): string {
  if (!at) return "earlier"
  const mins = Math.floor((Date.now() - at) / 60000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins} min ago`
  return new Date(at).toLocaleString(undefined, {
    hour: "numeric",
    minute: "2-digit",
    month: "short",
    day: "numeric",
  })
}

/** Plain-language reason a run stopped short of what was asked. */
const LIMIT_TEXT: Record<string, string> = {
  murk: "ran out of Murk",
  storage: "ran out of relic storage",
  gen_max: "hit the roll cap",
}

// --- Purchase-run progress stepper -------------------------------------------

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
  const { showErrorToast } = useCustomToast()
  const navigate = useNavigate()
  // Staged diff for this slot. Murk is emulated LIVE against it: the wallet
  // shown — and the wallet a new run spends from — is the save's Murk plus the
  // staged adjustment (every committed rites batch + staged-sell refunds).
  // Batches STACK, so an earlier batch's spend rides along with the request
  // and the next one buys from what is actually left.
  const pending = usePendingSlot(slotIndex)
  const pendingSells = pending.sells
  const staged = murkAdjustment(pending)
  const effMurks = effectiveMurks(murks, pending)
  const [stopMode, setStopMode] = useState<StopMode>("fixed")
  const [budget, setBudget] = useState<number>(
    Math.min(effMurks ?? 100000, 100000),
  )
  // "Spend down to" target: the mirror of a budget, for wallets big enough
  // that "how much is left" is the number you actually think in.
  const [targetMurk, setTargetMurk] = useState<number>(0)
  const [qty, setQty] = useState<Record<string, number>>({
    n103: 50,
    d103: 0,
    n102: 0,
    d102: 0,
  })
  const [inclusion, setInclusion] = useState<Rule[]>([])
  const [exclusion, setExclusion] = useState<Rule[]>([])
  const [busy, setBusy] = useState(false)
  // The last run's plan — kept only for the warnings that depend on it
  // (export capacity, storage). The PURCHASES themselves are read from the
  // staged batches below, so they survive navigation.
  const [plan, setPlan] = useState<PlanResponse | null>(null)
  const [selectedBuilds, setSelectedBuilds] = useState<Set<string>>(new Set())
  const buildGroups = useMemo(
    () => groupBuildsByCharacter(buildsForm.options),
    [buildsForm.options],
  )
  const [progress, setProgress] = useState<ProgressEvt | null>(null)
  // How many builds the in-flight run was started with (selection can change
  // while it runs); >0 switches the busy UI to the multi-step indicator.
  const [runBuildCount, setRunBuildCount] = useState(0)
  // Build-match depth: keep a purchase only if it lands in one of a build's
  // top-N ranked loadouts. 10 = the optimizer page's full list (widest).
  const [topN, setTopN] = useState(10)
  // Last event per phase, so completed steps keep their final N/N count.
  const seenRef = useRef<Record<string, ProgressEvt>>({})
  const abortRef = useRef<AbortController | null>(null)
  // Refund just credited by trashing a relic, flashed on the wallet so the
  // Murk coming back is visible where the Murk lives (the toast scrolls away).
  const [refundFlash, setRefundFlash] = useState<number | null>(null)
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(
    () => () => {
      if (flashTimer.current) clearTimeout(flashTimer.current)
    },
    [],
  )

  // Committed batches, newest first, each with the relics it still holds.
  const batches = useMemo(() => {
    const byBatch = new Map<string, MintSpec[]>()
    for (const m of pending.mints) {
      const key = m.batchId ?? ""
      const arr = byBatch.get(key)
      if (arr) arr.push(m)
      else byBatch.set(key, [m])
    }
    return [...pending.batches]
      .reverse()
      .map((b) => ({ batch: b, mints: byBatch.get(b.id) ?? [] }))
  }, [pending.mints, pending.batches])
  const stagedKept = pending.mints.length
  const stagedSpend = pending.batches.reduce((n, b) => n + b.murkDelta, 0)

  const activeBuckets = BUCKETS.filter((b) => (qty[b.key] ?? 0) > 0)
  const fixedCost = activeBuckets.reduce(
    (n, b) => n + (qty[b.key] ?? 0) * b.cost,
    0,
  )
  // Wallet a NEW run starts from = the live effective wallet: earlier batches
  // have already been paid for, so they must count against affordability.
  const planMurks = effMurks
  const overspend =
    stopMode === "fixed" && planMurks != null && fixedCost > planMurks
  // What a cycle run is actually allowed to spend, and the cheapest relic it
  // could spend it on. Less than one relic's price means the run can only ever
  // buy nothing — most easily hit by asking to spend down TO more Murk than
  // you hold, which is a no-op, not a purchase.
  const cheapestCost = activeBuckets.reduce(
    (n, b) => Math.min(n, b.cost),
    Number.POSITIVE_INFINITY,
  )
  const spendable =
    stopMode === "budget"
      ? Math.max(0, budget)
      : stopMode === "murk_target"
        ? Math.max(0, (effMurks ?? 0) - Math.max(0, targetMurk))
        : (effMurks ?? 0)
  const nothingToSpend =
    stopMode !== "fixed" && activeBuckets.length > 0 && spendable < cheapestCost
  const nothingToSpendWhy =
    stopMode === "murk_target"
      ? (effMurks ?? 0) <= Math.max(0, targetMurk)
        ? `— you already have less than ${formatMurks(Math.max(0, targetMurk))} Murk`
        : "— not enough above the target to buy even one relic"
      : stopMode === "budget"
        ? "— the budget is under one relic's price"
        : "— not enough Murk for a single relic"

  async function buyRelics() {
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
    if (stopMode === "murk_target")
      form.append("target_murk", String(Math.max(0, targetMurk)))
    // The whole staged diff rides along so the run happens in the world the
    // app is actually in: sold relics are gone (slots freed, refunds
    // spendable), earlier batches' purchases are owned (they hold storage and
    // dedup a re-rolled copy to a dud) and already paid for, and staged
    // bookmarks drive the protected-sell gate (un-bookmark + trash in one
    // session must not 422).
    const stagedDiff = stagedFields(pending)
    if (pendingSells.length)
      form.append("sold_handles", JSON.stringify(pendingSells))
    if (stagedDiff.staged_mints.length)
      form.append("staged_mints", JSON.stringify(stagedDiff.staged_mints))
    if (pending.murkDelta)
      form.append("staged_murk_delta", String(pending.murkDelta))
    // Which batch this is. The server folds it into the roll seed, so this run
    // buys NEW relics rather than re-viewing the last batch's stream — while
    // re-running after cancelling a batch replays that batch exactly.
    form.append("roll_epoch", String(nextRollEpoch(pending)))
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
      // The plan is computed from the FILE we sent; every number on this page
      // comes from the uploaded save's profile. `murk_before` is the server's
      // effective wallet — max(0, save Murk + the staged batch delta) — so if
      // it disagrees with what this profile holds, the file being read is NOT
      // this save. That happens when the in-session file is gone (a reload)
      // and the durable recovery backup stands in for it, or when an older
      // file is still selected: the run then silently plans against a
      // different save's Murk and inventory, and staging its purchases would
      // attach relics to the wrong save. Refuse rather than guess.
      const expectedBefore =
        murks == null
          ? null
          : Math.max(0, murks + Math.min(0, pending.murkDelta))
      if (expectedBefore != null && result.murk_before !== expectedBefore) {
        showErrorToast(
          `The save file being read holds ${formatMurks(result.murk_before)} ` +
            `Murk, but this save has ${formatMurks(expectedBefore)} — it is ` +
            "not the save shown here. Re-upload your save, then buy again. " +
            "Nothing was bought or staged.",
        )
        return
      }
      setPlan(result)
      // The roll IS the purchase: the batch commits the moment it's revealed,
      // mirroring the game, where a shop roll can't be previewed and declined.
      // Every keeper is kept (the walk the plan settled); trashing one sells
      // it back. The batch is appended — running again is another trip to the
      // shop, with its own rolls and its own bill.
      commitBatch(result, runLabel())
      // The keepers are owned now, so every build is being scored against an
      // inventory it has not seen. Say so and offer the way to fix it rather
      // than re-optimizing the whole library behind the user's back — a
      // parameter sweep would fire it on every re-roll.
      if (result.generated === 0) {
        // Nothing was bought, so there is no batch and no cost — say why
        // rather than reporting a success with an empty result.
        toast.warning("Nothing was bought", {
          description:
            result.limited_by === "storage"
              ? "Relic storage is full — trash relics from the Inventory (or stage sells) and run again."
              : `There was nothing to spend at these settings${
                  stopMode === "murk_target"
                    ? `: your Murk is already at or below ${formatMurks(
                        Math.max(0, targetMurk),
                      )}.`
                    : "."
                }`,
        })
        return
      }
      toast.success("Success!", {
        description:
          `Batch ${readSlot(slotIndex).batches.length}: bought ` +
          `${result.generated} relic(s), kept ${result.kept}` +
          ` for ${formatMurks(-result.murk_delta)} Murk.` +
          (result.kept > 0
            ? " Your builds haven't been optimized with them yet."
            : ""),
        action:
          result.kept > 0
            ? {
                label: "Optimize builds",
                onClick: () => navigate({ to: "/builds" }),
              }
            : undefined,
      })
    } catch (err) {
      if ((err as Error)?.name !== "AbortError")
        showErrorToast(
          err instanceof Error ? err.message : "Failed to buy relics.",
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

  /** What this run asked for, shown on the batch receipt. */
  function runLabel(): string {
    const names = activeBuckets.map((b) => b.label).join(" + ") || "relics"
    if (stopMode === "fixed") {
      const n = activeBuckets.reduce((t, b) => t + (qty[b.key] ?? 0), 0)
      return `Buy ${n} × ${names}`
    }
    if (stopMode === "budget")
      return `Spend ${formatMurks(budget)} Murk on ${names}`
    if (stopMode === "murk_target")
      return `Spend down to ${formatMurks(targetMurk)} Murk on ${names}`
    return `Spend all Murk on ${names}`
  }

  /**
   * Commit the plan as a new batch: its keepers are minted, its net Murk is
   * spent. The plan's murk_delta already assumes every keeper is kept
   * (faithful buy-then-sell); trashing one later credits its sell value back.
   * Staged-sell refunds of OWNED relics are not in the delta — those credit
   * themselves through the export's sell step.
   */
  function commitBatch(p: PlanResponse, label: string) {
    const specs: RitesBatchSpec[] = p.keepers.map((k) => ({
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
      buildRanks: k.build_ranks,
      reason: k.reason,
    }))
    appendRitesBatch(slotIndex, specs, p.murk_delta, {
      rolled: p.generated,
      kept: p.kept,
      cost: p.murk_cost,
      refunded: p.murk_refunded,
      label,
      limitedBy: p.limited_by,
    })
  }

  function cancelBatch(id: string) {
    const droppedOps = removeRitesBatch(slotIndex, id)
    if (droppedOps.length)
      showErrorToast(
        `Removed staged loadout change(s) that used this batch: ${droppedOps.join(", ")}`,
      )
  }

  function trashRelic(mint: MintSpec) {
    const refund = sellValue(effectCountOf(mint.effects), mint.isDeep)
    removeMint(slotIndex, mint.id)
    setRefundFlash(refund)
    if (flashTimer.current) clearTimeout(flashTimer.current)
    flashTimer.current = setTimeout(() => setRefundFlash(null), 2500)
    toast.success(`Sold ${mint.name} back for ${formatMurks(refund)} Murk`, {
      description:
        "It was still bought, so the batch keeps the buy/sell spread.",
    })
  }

  return (
    <div className="space-y-6">
      {/* Balances (live: save Murk with the staged diff applied) */}
      <div className="flex flex-wrap gap-4 text-sm">
        <span className="inline-flex items-center gap-1.5">
          <Coins className="h-4 w-4 text-amber-500" />
          <strong
            className={cn(
              "transition-colors duration-300",
              refundFlash != null && "text-green-600 dark:text-green-500",
            )}
          >
            {effMurks != null ? formatMurks(effMurks) : "—"}
          </strong>{" "}
          Murk
          {refundFlash != null && (
            <output className="animate-in fade-in slide-in-from-bottom-1 text-xs font-medium text-green-600 dark:text-green-500">
              +{formatMurks(refundFlash)} refunded
            </output>
          )}
          {staged !== 0 && murks != null && (
            <span className="text-xs text-muted-foreground">
              (save {formatMurks(murks)} {staged < 0 ? "−" : "+"}{" "}
              {formatMurks(Math.abs(staged))} staged)
            </span>
          )}
        </span>
        {plan ? (
          // storage_left is 1950 − owned with the staged diff applied, i.e. it
          // already counts the purchases staged when the plan ran.
          <span className="text-muted-foreground">
            Storage:{" "}
            <strong>{(RELIC_CAP - plan.storage_left).toLocaleString()}</strong>{" "}
            / {RELIC_CAP.toLocaleString()}
            {plan.pending_sold > 0 &&
              ` (after ${plan.pending_sold} staged sell${plan.pending_sold === 1 ? "" : "s"})`}
          </span>
        ) : (
          <span className="text-muted-foreground">
            Storage cap: {RELIC_CAP}
          </span>
        )}
        {stagedKept > 0 && (
          <span className="text-muted-foreground">
            Staged purchases: <strong>{stagedKept}</strong> relic
            {stagedKept === 1 ? "" : "s"}
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
          <div className="space-y-2">
            {buildGroups.map((g) => (
              <div key={g.character || "__none__"} className="space-y-1">
                <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  {g.character || "No character"}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {g.builds.map((b) => {
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
              </div>
            ))}
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
              <SelectItem value="murk_target">
                Murk is down to a set amount
              </SelectItem>
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
          {stopMode === "murk_target" && (
            <>
              <input
                type="number"
                min={0}
                max={effMurks ?? undefined}
                value={targetMurk}
                onChange={(e) => setTargetMurk(Number(e.target.value))}
                className="w-32 rounded-md border bg-background px-2 py-1 text-sm"
                aria-label="Murk to stop at"
              />
              <span className="text-xs text-muted-foreground">
                Murk left (not the amount to spend)
              </span>
            </>
          )}
        </div>

        {stopMode !== "fixed" && (
          <p className="text-xs text-muted-foreground">
            Pick one flatstone type — the buy/sell cycle runs on it until{" "}
            {stopMode === "budget"
              ? "the budget is spent"
              : stopMode === "murk_target"
                ? "your Murk is down to the amount above"
                : "your Murk runs out"}
            . Duds are sold back on the spot and their refunds fund the next
            buy, exactly as at the shop.
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
                ? `Spend ${formatMurks(budget)} Murk`
                : stopMode === "murk_target"
                  ? `Spend ${formatMurks(
                      Math.max(0, (effMurks ?? 0) - Math.max(0, targetMurk)),
                    )} Murk (down to ${formatMurks(Math.max(0, targetMurk))})`
                  : "Spend all your Murk"}
            {overspend && (
              <span className="ml-2 text-red-500">— more than you have</span>
            )}
            {nothingToSpend && (
              <span className="ml-2 text-amber-600 dark:text-amber-500">
                {nothingToSpendWhy}
              </span>
            )}
          </span>
          <Button
            onClick={buyRelics}
            disabled={busy || overspend || nothingToSpend}
            className="gap-1.5"
          >
            <Sparkles className="h-4 w-4" />
            {busy ? "Buying…" : "Buy relics"}
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

      {/* Warnings about the most recent run (they concern the NEXT export) */}
      {plan?.limited_by === "storage" && (
        <p className="text-xs text-muted-foreground">
          Storage is full — trash relics from the Inventory and run again.
          Staged sells free space for this simulation.
        </p>
      )}
      {plan && plan.kept > plan.add_capacity && (
        <p className="text-xs text-amber-600 dark:text-amber-500">
          Your next export could mint {plan.add_capacity} more relic(s) and this
          batch kept {plan.kept} — the server already counts staged sells (each
          frees a slot) and earlier staged purchases (each claims one). Stage
          more sells, or export what you have before buying more.
        </p>
      )}

      {/* Purchases — read from the staged batches, so they survive navigation */}
      {batches.length > 0 && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-sm font-medium">
              Purchases{" "}
              <span className="text-xs font-normal text-muted-foreground">
                — {batches.length} batch{batches.length === 1 ? "" : "es"} on
                this save, newest first
              </span>
            </h2>
            <span className="text-xs text-muted-foreground">
              {stagedKept} relic{stagedKept === 1 ? "" : "s"} staged ·{" "}
              {formatMurks(Math.abs(stagedSpend))} Murk{" "}
              {stagedSpend > 0 ? "gained" : "spent"}
            </span>
          </div>
          {batches.map(({ batch, mints }, i) => (
            <BatchCard
              key={batch.id}
              batch={batch}
              number={batches.length - i}
              mints={mints}
              effectMap={effectMap}
              // Only the newest batch is expanded: a long session stacks a lot
              // of them, and the older ones are receipts you scroll past.
              defaultOpen={i === 0}
              onCancel={() => cancelBatch(batch.id)}
              onTrash={trashRelic}
            />
          ))}
          <p className="text-xs text-muted-foreground">
            Every batch above is committed to your Changes — buying is binding,
            exactly as in-game. Trashing a relic sells it back for its refund;
            cancelling a batch un-buys the whole trip. Running again buys a new
            batch of relics.
          </p>
        </div>
      )}
    </div>
  )
}

/** One committed purchase run: its receipt, and the relics it still holds. */
function BatchCard({
  batch,
  number,
  mints,
  effectMap,
  defaultOpen,
  onCancel,
  onTrash,
}: {
  batch: RitesBatch
  number: number
  mints: MintSpec[]
  effectMap: Map<number, string>
  defaultOpen: boolean
  onCancel: () => void
  onTrash: (m: MintSpec) => void
}) {
  const [open, setOpen] = useState(defaultOpen)
  const sold = Math.max(0, batch.rolled - mints.length)
  return (
    <div className="space-y-3 rounded-lg border p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-left"
          aria-expanded={open}
        >
          <ChevronRight
            className={cn(
              "h-3.5 w-3.5 self-center text-muted-foreground transition-transform",
              open && "rotate-90",
            )}
          />
          <span className="text-sm font-medium">Batch {number}</span>
          <span className="text-xs text-muted-foreground">
            {formatWhen(batch.at)} · {batch.label}
          </span>
        </button>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
          <span>
            Rolled <strong>{batch.rolled}</strong> · kept{" "}
            <strong className="text-green-600 dark:text-green-500">
              {mints.length}
            </strong>{" "}
            · sold {sold}
          </span>
          <span>
            <strong>
              {batch.murkDelta > 0 ? "+" : "−"}
              {formatMurks(Math.abs(batch.murkDelta))}
            </strong>{" "}
            Murk
            {batch.cost > 0 && (
              <span className="text-muted-foreground">
                {" "}
                (−{formatMurks(batch.cost)} +{formatMurks(batch.refunded)})
              </span>
            )}
          </span>
          {batch.limitedBy && (
            <Badge variant="secondary">
              {LIMIT_TEXT[batch.limitedBy] ?? batch.limitedBy}
            </Badge>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={onCancel}
            className="h-7 gap-1.5 text-xs text-muted-foreground"
          >
            <X className="h-3.5 w-3.5" />
            Cancel batch
          </Button>
        </div>
      </div>

      {!open ? null : mints.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Nothing kept — every relic this batch rolled was sold back. The
          buy/sell spread above is committed to your Changes and exports with
          it: rolling is buying, exactly as in-game.
        </p>
      ) : (
        // Same presentation as the Inventory table (RelicNameCell +
        // EffectList), with a "Kept for" column naming each keeper's builds.
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Relic</TableHead>
              <TableHead>Effects</TableHead>
              <TableHead className="w-44">Kept for</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {mints.map((m) => {
              const effectsCell = EffectList({
                effectIds: m.effects,
                isCurse: false,
                effectMap,
              })
              const cursesCell = EffectList({
                effectIds: m.curses,
                isCurse: true,
                effectMap,
              })
              return (
                <TableRow key={m.id}>
                  <TableCell className="min-w-[180px]">
                    <RelicNameCell
                      name={m.name}
                      color={m.color}
                      tier={m.tier}
                      isDeep={m.isDeep}
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
                      {m.reason === "inclusion" && (
                        <Badge variant="default" className="text-[10px]">
                          kept by rule
                        </Badge>
                      )}
                      {m.reason === "kept" && (
                        <Badge variant="secondary" className="text-[10px]">
                          no build filter
                        </Badge>
                      )}
                      {(m.builds ?? []).map((b, bi) => {
                        const rank = m.buildRanks?.[bi] ?? 0
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
                                className={isBest ? "font-bold" : "opacity-70"}
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
                  <TableCell>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8 text-muted-foreground hover:text-destructive"
                      onClick={() => onTrash(m)}
                      title={`Sell ${m.name} back for ${formatMurks(
                        sellValue(effectCountOf(m.effects), m.isDeep),
                      )} Murk`}
                      aria-label={`Sell ${m.name} back`}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
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
  const buildOptions = labelBuilds(builds.data ?? [])
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
  const buildOptions = labelBuilds(builds)
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
