import { useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { Coins, Package, Sparkles, Trash2 } from "lucide-react"
import { Suspense, useMemo, useState } from "react"

import { BuildsService, GameService, SavesService } from "@/client"
import { EmptyState } from "@/components/Common/EmptyState"
import { buildEffectMap } from "@/components/RelicDisplay"
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
import useAuth from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"
import { addMints, toggleSell } from "@/lib/pendingChanges"
import { getOriginalBackupFile } from "@/lib/saveBackup"
import { getSaveFile } from "@/lib/saveFile"
import { formatMurks } from "@/lib/sellValue"

export const Route = createFileRoute("/_layout/rites")({
  component: RitesPage,
  head: () => ({
    meta: [{ title: "Relic Rites - Nightreign Relic Planner" }],
  }),
})

const EMPTY = 4294967295
const RELIC_CAP = 1950

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
}
type PlanResponse = {
  keepers: PlanKeeper[]
  generated: number
  kept: number
  duds: number
  murk_before: number
  murk_after: number
  murk_cost: number
  murk_refunded: number
  murk_delta: number
  limited_by: string | null
  add_capacity: number
  storage_left: number
  cull_candidates: number[]
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

function nameOf(map: Map<number, string>, id: number): string | null {
  if (id === EMPTY || id === 0 || id === -1) return null
  return map.get(id) ?? `Effect ${id}`
}

// --- The tool (authenticated: uses your saved builds) ----------------------

function RitesTool({
  slotIndex,
  murks,
  buildIds,
  effectMap,
}: {
  slotIndex: number
  murks: number
  buildIds: string[]
  effectMap: Map<number, string>
}) {
  const { showErrorToast, showSuccessToast } = useCustomToast()
  const [stopMode, setStopMode] = useState<StopMode>("fixed")
  const [budget, setBudget] = useState<number>(Math.min(murks, 100000))
  const [qty, setQty] = useState<Record<string, number>>({
    n103: 50,
    d103: 0,
    n102: 0,
    d102: 0,
  })
  const [busy, setBusy] = useState(false)
  const [plan, setPlan] = useState<PlanResponse | null>(null)
  const [selected, setSelected] = useState<Set<number>>(new Set())

  const activeBuckets = BUCKETS.filter((b) => (qty[b.key] ?? 0) > 0)
  const fixedCost = activeBuckets.reduce(
    (n, b) => n + (qty[b.key] ?? 0) * b.cost,
    0,
  )
  const overspend = stopMode === "fixed" && fixedCost > murks

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
    const form = new FormData()
    form.append("file", file, file.name || "save.sl2")
    form.append("slot_index", String(slotIndex))
    form.append("build_ids", JSON.stringify(buildIds))
    form.append("buckets", JSON.stringify(buckets))
    form.append("stop_mode", stopMode)
    if (stopMode === "budget") form.append("budget", String(budget))

    setBusy(true)
    try {
      const res = await fetch("/api/v1/saves/rites/plan", {
        method: "POST",
        headers: authHeaders(),
        body: form,
      })
      if (!res.ok) throw new Error(await planDetail(res))
      const data: PlanResponse = await res.json()
      setPlan(data)
      setSelected(new Set(data.keepers.map((_, i) => i)))
    } catch (err) {
      showErrorToast(
        err instanceof Error ? err.message : "Failed to find keepers.",
      )
    } finally {
      setBusy(false)
    }
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

  function cullUnused() {
    if (!plan?.cull_candidates.length) return
    for (const h of plan.cull_candidates) toggleSell(slotIndex, h)
    showSuccessToast(
      `Staged ${plan.cull_candidates.length} owned relic(s) for sale (no build uses them). Review in the Changes panel.`,
    )
  }

  return (
    <div className="space-y-6">
      {/* Balances */}
      <div className="flex flex-wrap gap-4 text-sm">
        <span className="inline-flex items-center gap-1.5">
          <Coins className="h-4 w-4 text-amber-500" />
          <strong>{formatMurks(murks)}</strong> Murk
        </span>
        <span className="text-muted-foreground">
          Storage room: {RELIC_CAP} cap
        </span>
      </div>

      {/* Buckets */}
      <div className="rounded-lg border p-4 space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm font-medium">Stop when</span>
          <Select
            value={stopMode}
            onValueChange={(v) => setStopMode(v as StopMode)}
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
              max={murks}
              value={budget}
              onChange={(e) => setBudget(Number(e.target.value))}
              className="w-32 rounded-md border bg-background px-2 py-1 text-sm"
              aria-label="Murk budget"
            />
          )}
        </div>

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
                  type="checkbox"
                  checked={(qty[b.key] ?? 0) > 0}
                  onChange={(e) =>
                    setQty((q) => ({ ...q, [b.key]: e.target.checked ? 1 : 0 }))
                  }
                  className="h-4 w-4"
                  aria-label={`Include ${b.label}`}
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
            {busy ? "Rolling…" : "Find keepers"}
          </Button>
        </div>
      </div>

      {/* Results */}
      {plan && (
        <div className="rounded-lg border p-4 space-y-4">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
            <span>
              Rolled <strong>{plan.generated}</strong> · kept{" "}
              <strong className="text-green-600 dark:text-green-500">
                {plan.kept}
              </strong>{" "}
              · sold {plan.duds}
            </span>
            <span>
              Murk: {formatMurks(plan.murk_before)} →{" "}
              <strong>{formatMurks(plan.murk_after)}</strong>{" "}
              <span className="text-muted-foreground">
                (−{formatMurks(plan.murk_cost)} +
                {formatMurks(plan.murk_refunded)})
              </span>
            </span>
            {plan.limited_by && (
              <Badge variant="secondary">limited by {plan.limited_by}</Badge>
            )}
          </div>

          {plan.keepers.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No keepers found — none of the rolled relics improved a build's
              best loadout. Try a larger batch or a different relic type.
            </p>
          ) : (
            <ul className="space-y-1">
              {plan.keepers.map((k, i) => (
                <li
                  key={`${k.item_id}-${i}`}
                  className="flex items-start gap-2 rounded-md border px-3 py-2 text-sm"
                >
                  <input
                    type="checkbox"
                    checked={selected.has(i)}
                    onChange={() =>
                      setSelected((s) => {
                        const n = new Set(s)
                        n.has(i) ? n.delete(i) : n.add(i)
                        return n
                      })
                    }
                    className="mt-1 h-4 w-4"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="font-medium">
                      {k.name}{" "}
                      <span className="text-xs text-muted-foreground">
                        {k.tier} {k.color}
                        {k.is_deep ? " · Deep" : ""}
                      </span>
                    </div>
                    <div className="text-xs text-muted-foreground truncate">
                      {k.effects
                        .map((e) => nameOf(effectMap, e))
                        .filter(Boolean)
                        .join(" · ")}
                      {k.curses.some((c) => nameOf(effectMap, c)) && (
                        <span className="text-red-500">
                          {" "}
                          ⛧{" "}
                          {k.curses
                            .map((c) => nameOf(effectMap, c))
                            .filter(Boolean)
                            .join(" · ")}
                        </span>
                      )}
                    </div>
                    {k.builds.length > 0 && (
                      <div className="mt-0.5 flex flex-wrap gap-1">
                        {k.builds.map((b) => (
                          <Badge
                            key={b}
                            variant="outline"
                            className="text-[10px]"
                          >
                            {b}
                          </Badge>
                        ))}
                      </div>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}

          <div className="flex flex-wrap items-center justify-between gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={cullUnused}
              disabled={!plan.cull_candidates.length}
              className="gap-1.5"
            >
              <Trash2 className="h-4 w-4" />
              Cull {plan.cull_candidates.length} unused owned relic(s)
            </Button>
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

// Local sell-value mirror (matches nrplanner.writer.sell_value; deep x2).
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

function RitesBody() {
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
  const { data: effectsData } = useSuspenseQuery({
    queryKey: ["game", "effects"],
    queryFn: () => GameService.getEffects(),
    staleTime: Number.POSITIVE_INFINITY,
  })
  const effectMap = useMemo(
    () => buildEffectMap((effectsData ?? []) as unknown[]),
    [effectsData],
  )

  const [selectedId, setSelectedId] = useState<string | null>(
    profiles.data?.[0]?.id ?? null,
  )

  if (!profiles.data?.length) {
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
  const buildIds = (builds.data ?? []).map((b) => b.id)
  if (!buildIds.length) {
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
        Relic Rites keeps only relics your builds would use — define at least
        one build first.
      </EmptyState>
    )
  }

  const selected =
    profiles.data.find((p) => p.id === selectedId) ?? profiles.data[0]

  return (
    <div className="space-y-4">
      {profiles.data.length > 1 && (
        <Select value={selected.id} onValueChange={setSelectedId}>
          <SelectTrigger className="w-48">
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
        buildIds={buildIds}
        effectMap={effectMap}
      />
    </div>
  )
}

function RitesPage() {
  const { user } = useAuth()

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Relic Rites</h1>
        <p className="text-muted-foreground mt-1">
          Bulk-buy relics the way the game does, keep only the ones your builds
          use, and sell the rest for Murk — the whole grind, in one click.
          Nothing changes until you export from the Changes panel.
        </p>
      </div>
      {user ? (
        <Suspense fallback={<Skeleton className="h-48 w-full" />}>
          <RitesBody />
        </Suspense>
      ) : (
        <EmptyState icon={Sparkles} title="Sign in to use Relic Rites">
          Relic Rites keeps relics based on your saved builds, so it needs an
          account.{" "}
          <a href="/login" className="underline">
            Sign in
          </a>{" "}
          to get started.
        </EmptyState>
      )}
    </div>
  )
}
