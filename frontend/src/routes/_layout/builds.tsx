import { zodResolver } from "@hookform/resolvers/zod"
import {
  useMutation,
  useQuery,
  useQueryClient,
  useSuspenseQuery,
} from "@tanstack/react-query"
import {
  createFileRoute,
  Link,
  Outlet,
  useRouterState,
} from "@tanstack/react-router"
import {
  BookMarked,
  Copy,
  Layers,
  Loader2,
  MoreVertical,
  Pencil,
  Plus,
  Star,
  Trash2,
  X,
  Zap,
} from "lucide-react"
import { Suspense, useMemo, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import {
  type BuildChange,
  type BuildSnapshotSummary,
  BuildsService,
  type FeaturedBuildPublic,
  type LoadoutRank,
  OptimizeService,
  SavesService,
} from "@/client"
import { ChangeRelicGroups } from "@/components/ChangeRelics"
import { EmptyState } from "@/components/Common/EmptyState"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import useAuth, { isLoggedIn } from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"
import { useEffectMap } from "@/hooks/useEffectMap"
import {
  DEFAULT_GROUPS,
  type LocalBuild,
  useLocalBuilds,
} from "@/hooks/useLocalBuilds"
import {
  changeSummaryText,
  describeBuildChange,
  isChangeNews,
  rawScoreTooltip,
} from "@/lib/buildChange"
import { CHARACTER_NAMES } from "@/lib/constants"
import { useBuildOptimizeStatus } from "@/lib/optimizeJobs"
import {
  effectiveLoadouts,
  stagedFields,
  stagedKey,
  usePendingSlot,
} from "@/lib/pendingChanges"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/builds")({
  component: BuildsPage,
  head: () => ({
    meta: [{ title: "Optimizer - Nightreign Relic Planner" }],
  }),
})

const newBuildSchema = z.object({
  name: z.string().min(1, "Name is required").max(255),
  character: z.string().min(1, "Character is required"),
})
type NewBuildForm = z.infer<typeof newBuildSchema>

// --- Shared build form dialog (used by both auth and anon) ---

interface NewBuildDialogProps {
  onCreate: (data: NewBuildForm) => void
  isPending?: boolean
}

function NewBuildDialogContent({ onCreate, isPending }: NewBuildDialogProps) {
  const [open, setOpen] = useState(false)
  const form = useForm<NewBuildForm>({
    resolver: zodResolver(newBuildSchema),
    defaultValues: { name: "", character: "Wylder" },
  })

  function handleSubmit(data: NewBuildForm) {
    onCreate(data)
    form.reset()
    setOpen(false)
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm">
          <Plus className="h-4 w-4 mr-1" />
          New Build
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create Build</DialogTitle>
        </DialogHeader>
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(handleSubmit)}
            className="space-y-4"
          >
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Name</FormLabel>
                  <FormControl>
                    <Input placeholder="e.g. Fire Wylder" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="character"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Character</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {CHARACTER_NAMES.map((c) => (
                        <SelectItem key={c} value={c}>
                          {c}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />
            <Button type="submit" className="w-full" disabled={isPending}>
              {isPending ? "Creating…" : "Create"}
            </Button>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

// --- Shared build card renderer ---

interface BuildCardData {
  id: string
  name: string
  character: string
  groups?: { effects: number[]; families: string[] }[]
  required_effects?: number[]
  updated_at?: string | null
  is_featured?: boolean
}

/**
 * "The loadout I have saved in-game is this build's suggestion #N."
 *
 * Deliberately flat across ranks — same wording, same colour, whether the save
 * matches #1 or #7.  The optimizer SUGGESTS arrangements; the top ten exist
 * because the player may well prefer #4 (fewer curses, relics they'd rather
 * not move, a spread they like), so a rank is a position in a list, not a
 * score to chase.  It reads the same as the optimize page's card badge, which
 * is the same fact seen from the other side.
 *
 * Rendered only on a match: "never saved this build" and "saved something
 * outside the top ten" are both silence, since neither is news worth a badge.
 */
function SavedLoadoutBadge({ rank }: { rank: LoadoutRank }) {
  const label = rank.loadout_name || "(unnamed)"
  return (
    <span
      className="mt-1.5 inline-flex max-w-full items-center gap-1 text-[11px] font-medium text-muted-foreground"
      title={`Your in-game loadout "${label}" is suggestion #${rank.rank} of ${rank.total} for this build.`}
    >
      <BookMarked className="h-3 w-3 shrink-0" />
      <span className="truncate">
        Saved: {label} · #{rank.rank} of {rank.total}
      </span>
    </span>
  )
}

function BuildCard({
  build,
  onDelete,
  onRename,
  onChangeCharacter,
  onDuplicate,
  onToggleFeatured,
  isDeleting,
  summary,
  loadoutRank,
}: {
  build: BuildCardData
  onDelete: (id: string) => void
  onRename: (id: string, newName: string) => void
  onChangeCharacter: (id: string, character: string) => void
  onDuplicate?: (id: string) => void
  onToggleFeatured?: (id: string) => void
  isDeleting?: boolean
  summary?: BuildSnapshotSummary
  /** Set when one of the character's in-game loadouts reproduces one of this
   *  build's optimizer results. Absent = nothing to say (see the badge). */
  loadoutRank?: LoadoutRank
}) {
  const [draftName, setDraftName] = useState(build.name)
  const [deleteOpen, setDeleteOpen] = useState(false)
  // Live status while a background save re-optimization is touching this build.
  const optimizeStatus = useBuildOptimizeStatus(build.id)

  const effectCount = (build.groups ?? []).reduce(
    (acc, g) => acc + g.effects.length,
    0,
  )

  function commitRename() {
    const trimmed = draftName.trim()
    if (trimmed && trimmed !== build.name) {
      onRename(build.id, trimmed)
    } else {
      setDraftName(build.name)
    }
  }

  return (
    <>
      <Card className="@container flex flex-col gap-3 px-6 py-4 min-h-[140px]">
        {/* Header: name + actions */}
        <div className="flex items-center gap-2">
          <input
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault()
                e.currentTarget.blur()
              }
              if (e.key === "Escape") {
                setDraftName(build.name)
                e.currentTarget.blur()
              }
            }}
            onBlur={commitRename}
            className="text-base font-semibold bg-transparent border-b border-transparent hover:border-muted-foreground/30 focus:border-primary focus:outline-none focus:ring-0 py-0.5 min-w-0 flex-1 truncate transition-colors"
          />
          {optimizeStatus === "optimizing" ? (
            <span className="shrink-0 inline-flex items-center gap-1 text-xs font-medium text-primary">
              <Loader2 className="h-3 w-3 animate-spin" />
              Optimizing…
            </span>
          ) : (
            (() => {
              // Subtle at-a-glance marker: only the score/pin-moving changes
              // (neutral "rearranged" lives in the changes-since-last-save list).
              const d = describeBuildChange(summary?.change)
              if (!d || d.tone === "neutral") return null
              const Icon = d.icon
              return (
                <span
                  className={`shrink-0 inline-flex items-center gap-0.5 text-xs font-medium ${d.textClass}`}
                  title={changeSummaryText(d)}
                >
                  <Icon className="h-3 w-3" />
                  {d.headline}
                </span>
              )
            })()
          )}
          <div className="flex items-center gap-1 shrink-0">
            <Button
              asChild
              variant="ghost"
              size="icon"
              className="hidden @[280px]:inline-flex h-8 w-8 text-primary hover:text-primary"
              title="Optimize build"
            >
              <Link
                to="/builds/$buildId/optimize"
                params={{ buildId: build.id }}
              >
                <Zap className="h-4 w-4" />
              </Link>
            </Button>
            <Button
              asChild
              variant="ghost"
              size="icon"
              className="hidden @[280px]:inline-flex h-8 w-8"
              title="Edit build"
            >
              <Link to="/builds/$buildId/edit" params={{ buildId: build.id }}>
                <Pencil className="h-4 w-4" />
              </Link>
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon" className="h-8 w-8">
                  <MoreVertical className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem asChild>
                  <Link
                    to="/builds/$buildId/optimize"
                    params={{ buildId: build.id }}
                  >
                    <Zap className="mr-2 h-4 w-4 text-primary" />
                    Optimize
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link
                    to="/builds/$buildId/edit"
                    params={{ buildId: build.id }}
                  >
                    <Pencil className="mr-2 h-4 w-4" />
                    Edit
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                {onToggleFeatured && (
                  <DropdownMenuItem onClick={() => onToggleFeatured(build.id)}>
                    <Star
                      className={`mr-2 h-4 w-4 ${build.is_featured ? "fill-current text-gold" : "text-muted-foreground"}`}
                    />
                    {build.is_featured ? "Unfeature" : "Feature"}
                  </DropdownMenuItem>
                )}
                {onDuplicate && (
                  <DropdownMenuItem onClick={() => onDuplicate(build.id)}>
                    <Copy className="mr-2 h-4 w-4" />
                    Duplicate
                  </DropdownMenuItem>
                )}
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  onClick={() => setDeleteOpen(true)}
                  className="text-destructive focus:bg-destructive focus:text-destructive-foreground"
                >
                  <Trash2 className="mr-2 h-4 w-4" />
                  Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>

        {/* Body: effect count */}
        <div className="flex-1 flex flex-col items-center justify-center">
          {effectCount > 0 ? (
            <>
              <span className="text-3xl font-bold text-primary">
                {effectCount}
              </span>
              <span className="text-[10px] text-muted-foreground uppercase tracking-wider font-semibold">
                Prioritized Effect{effectCount !== 1 ? "s" : ""}
              </span>
            </>
          ) : (
            <div className="text-center text-muted-foreground">
              <span className="text-sm">No prioritized effects</span>
              <br />
              <span className="text-[10px] uppercase tracking-wider font-semibold opacity-75">
                Edit build to add
              </span>
            </div>
          )}
          {loadoutRank && <SavedLoadoutBadge rank={loadoutRank} />}
        </div>

        {/* Footer: character + date */}
        <div className="mt-auto flex items-center justify-between text-xs text-muted-foreground">
          <Select
            value={build.character}
            onValueChange={(value) => onChangeCharacter(build.id, value)}
          >
            <SelectTrigger className="h-auto border-none bg-transparent p-0 shadow-none text-muted-foreground text-xs font-medium hover:text-foreground transition-colors [&>svg]:h-3 [&>svg]:w-3 [&>svg]:opacity-50 hover:[&>svg]:opacity-100 w-auto gap-1">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CHARACTER_NAMES.map((c) => (
                <SelectItem key={c} value={c}>
                  {c}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {build.updated_at && (
            <span>
              Updated {new Date(build.updated_at).toLocaleDateString()}
            </span>
          )}
        </div>
      </Card>

      {/* Delete confirmation — outside Card to avoid layout interference */}
      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete "{build.name}"?</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            This action cannot be undone.
          </p>
          <div className="flex justify-end gap-2 mt-4">
            <Button variant="outline" onClick={() => setDeleteOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                onDelete(build.id)
                setDeleteOpen(false)
              }}
              disabled={isDeleting}
            >
              {isDeleting ? "Deleting…" : "Delete"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}

// --- Featured build card ---

function FeaturedBuildCard({
  build,
  onClone,
  isCloning,
  isSuperuser,
  onToggleFeatured,
}: {
  build: FeaturedBuildPublic
  onClone: (build: FeaturedBuildPublic) => void
  isCloning?: boolean
  isSuperuser?: boolean
  onToggleFeatured?: (buildId: string) => void
}) {
  const b = build as any
  const effectCount = ((b.groups ?? []) as { effects: number[] }[]).reduce(
    (acc, g) => acc + g.effects.length,
    0,
  )

  return (
    <Card className="flex flex-col min-h-[160px]">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base truncate">{build.name}</CardTitle>
          <div className="flex items-center gap-1 shrink-0">
            {isSuperuser && onToggleFeatured && (
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 text-gold"
                onClick={() => onToggleFeatured(build.id)}
                title="Unfeature build"
              >
                <Star className="h-4 w-4 fill-current" />
              </Button>
            )}
            <Button
              size="sm"
              variant="outline"
              onClick={() => onClone(build)}
              disabled={isCloning}
            >
              {isCloning ? "Cloning…" : "Use This Build"}
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="flex-1 flex flex-col justify-center py-4">
        {effectCount > 0 ? (
          <div className="flex flex-col items-center gap-1 text-center">
            <span className="text-3xl font-bold text-primary">
              {effectCount}
            </span>
            <span className="text-[10px] text-muted-foreground uppercase tracking-wider font-semibold">
              Prioritized Effect{effectCount !== 1 ? "s" : ""}
            </span>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-1 text-center text-muted-foreground">
            <span className="text-sm">No prioritized effects</span>
          </div>
        )}
      </CardContent>
      <div className="mt-auto px-6 pb-4 pt-0 flex items-center justify-between text-xs text-muted-foreground">
        <span className="font-medium">{build.character}</span>
        {build.owner_name && <span>by {build.owner_name}</span>}
      </div>
    </Card>
  )
}

// --- Suggested builds section (visible to everyone) ---

function SuggestedBuildsContent() {
  const { data } = useSuspenseQuery({
    queryKey: ["builds", "featured"],
    queryFn: () => BuildsService.listFeaturedBuilds(),
  })

  const { user } = useAuth()
  const isSuperuser = user?.is_superuser ?? false
  const loggedIn = isLoggedIn()
  const { createFull } = useLocalBuilds()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const queryClient = useQueryClient()

  const cloneMutation = useMutation({
    mutationFn: (buildId: string) => BuildsService.cloneBuild({ buildId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["builds"] })
      showSuccessToast("Build copied to your account.")
    },
    onError: handleError.bind(showErrorToast),
  })

  const toggleMutation = useMutation({
    mutationFn: (buildId: string) => BuildsService.toggleFeatured({ buildId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["builds", "featured"] })
      queryClient.invalidateQueries({ queryKey: ["builds"] })
    },
    onError: handleError.bind(showErrorToast),
  })

  function handleClone(build: FeaturedBuildPublic) {
    if (loggedIn) {
      cloneMutation.mutate(build.id)
    } else {
      const b = build as any
      createFull({
        name: build.name,
        character: build.character,
        groups: b.groups ?? [],
        required_effects: b.required_effects ?? [],
        required_families: b.required_families ?? [],
        excluded_effects: b.excluded_effects ?? [],
        excluded_families: b.excluded_families ?? [],
        include_deep: build.include_deep,
        curse_max: build.curse_max,
        default_curse_weight: b.default_curse_weight ?? 0,
        pinned_relics: b.pinned_relics ?? [],
      })
      showSuccessToast("Build saved to your browser.")
    }
  }

  if (!data.data?.length) return null

  return (
    <div className="space-y-3">
      <div>
        <h2 className="text-2xl font-semibold">Suggested Builds</h2>
        <p className="text-muted-foreground mt-1">
          Community-curated builds to get you started.
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {data.data.map((build) => (
          <FeaturedBuildCard
            key={build.id}
            build={build}
            onClone={handleClone}
            isCloning={cloneMutation.isPending}
            isSuperuser={isSuperuser}
            onToggleFeatured={(id) => toggleMutation.mutate(id)}
          />
        ))}
      </div>
    </div>
  )
}

function SuggestedBuildsSection() {
  return (
    <Suspense fallback={<Skeleton className="h-32 w-full" />}>
      <SuggestedBuildsContent />
    </Suspense>
  )
}

// --- Authenticated build section (API-backed) ---

// --- "Changes since you last looked" — durable, server-backed change list ---

function ChangeRow({
  buildId,
  name,
  change,
  effectMap,
  onDismiss,
}: {
  buildId: string
  name: string
  change: BuildChange
  effectMap: Map<number, string>
  onDismiss: (buildId: string) => void
}) {
  const d = describeBuildChange(change)
  if (!d) return null
  const Icon = d.icon
  return (
    <li className="flex items-start justify-between gap-3 py-1.5">
      <div className="min-w-0">
        <Link
          to="/builds/$buildId/optimize"
          params={{ buildId }}
          className="font-medium hover:underline"
        >
          {name}
        </Link>
        <div
          className={`flex items-center gap-1 text-sm ${d.textClass}`}
          title={rawScoreTooltip(d.rawScore)}
        >
          <Icon className="h-3.5 w-3.5 shrink-0" />
          <span>{d.headline}</span>
          {d.reliable === false && (
            <span className="text-xs opacity-70">(approximate)</span>
          )}
        </div>
        <ChangeRelicGroups groups={d.groups} effectMap={effectMap} />
        {d.note && (
          <p className="mt-0.5 text-xs text-muted-foreground">{d.note}</p>
        )}
      </div>
      <Button
        variant="ghost"
        size="icon"
        className="h-6 w-6 shrink-0 text-muted-foreground"
        title="Dismiss"
        onClick={() => onDismiss(buildId)}
      >
        <X className="h-3.5 w-3.5" />
      </Button>
    </li>
  )
}

function ChangesSinceLastSave({
  summaries,
  buildName,
  onDismiss,
}: {
  summaries: BuildSnapshotSummary[]
  buildName: (buildId: string) => string
  onDismiss: (buildId: string) => void
}) {
  const effectMap = useEffectMap()
  // Unread changes worth surfacing, deduped per build: a newer save, or relics
  // bought in Relic Rites (owned, but still owed to the save file). Build edits
  // and game-data bumps re-baseline silently and never appear here.
  const seen = new Set<string>()
  const rows: { buildId: string; change: BuildChange }[] = []
  for (const s of summaries) {
    if (s.reviewed !== false || !s.build_id || seen.has(s.build_id)) continue
    const change = s.change
    if (!change) continue
    if (!isChangeNews(change)) continue
    if (!describeBuildChange(change)) continue
    seen.add(s.build_id)
    rows.push({ buildId: s.build_id, change })
  }
  if (rows.length === 0) return null

  return (
    <Card className="px-6 py-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Changes since you last looked</h2>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 text-xs text-muted-foreground"
          onClick={() => {
            for (const r of rows) onDismiss(r.buildId)
          }}
        >
          Dismiss all
        </Button>
      </div>
      <ul className="mt-1 divide-y">
        {rows.map((r) => (
          <ChangeRow
            key={r.buildId}
            buildId={r.buildId}
            name={buildName(r.buildId)}
            change={r.change}
            effectMap={effectMap}
            onDismiss={onDismiss}
          />
        ))}
      </ul>
    </Card>
  )
}

/**
 * Per-build "your in-game loadout is result #N", keyed by build id.
 *
 * Matched against the LIVE preset list (staged loadout edits composed in), so
 * a setup saved from the optimizer but not yet exported counts as saved — it
 * is what the user's save will hold. Ranks come from each build's cached
 * optimize, so `snapshotSig` (the summaries' updated_at set) is part of the
 * key: a re-optimize moves the results and must move the badge with them.
 */
function useLoadoutRanks(snapshotSig: string): Map<string, LoadoutRank> {
  const { data: profilesData } = useQuery({
    queryKey: ["profiles"],
    queryFn: () => SavesService.listProfiles(),
    staleTime: 5 * 60 * 1000,
  })
  // The builds list has no profile picker; the optimize page defaults to the
  // first profile too, so the badge describes the same save the user optimizes
  // against by default.
  const profile = profilesData?.data?.[0]
  const { data: loadoutsData } = useQuery({
    queryKey: ["loadouts", profile?.id],
    queryFn: () => SavesService.getProfileLoadouts({ profileId: profile!.id }),
    enabled: !!profile,
    staleTime: 5 * 60 * 1000,
  })

  const pending = usePendingSlot(profile?.slot_index ?? null)
  const sig = stagedKey(pending)
  const loadouts = useMemo(
    () => effectiveLoadouts(loadoutsData?.data ?? [], pending),
    [loadoutsData, pending],
  )
  const loadoutsSig = JSON.stringify(loadouts)

  const { data } = useQuery({
    queryKey: ["loadout-ranks", profile?.id, sig, loadoutsSig, snapshotSig],
    queryFn: () =>
      OptimizeService.listLoadoutRanks({
        requestBody: {
          profile_id: profile!.id,
          ...stagedFields(pending),
          loadouts,
        },
      }),
    enabled: !!profile && loadouts.length > 0,
    staleTime: 5 * 60 * 1000,
  })

  return useMemo(() => {
    const m = new Map<string, LoadoutRank>()
    for (const r of data ?? []) m.set(r.build_id, r)
    return m
  }, [data])
}

function AuthBuildList() {
  const { data } = useSuspenseQuery({
    queryKey: ["builds"],
    queryFn: () => BuildsService.listBuilds(),
  })
  const { data: summaries } = useQuery({
    queryKey: ["build-summaries"],
    queryFn: () => OptimizeService.listBuildSummaries(),
  })
  const summaryByBuild = new Map<string, BuildSnapshotSummary>()
  for (const s of summaries ?? []) {
    if (s.build_id) summaryByBuild.set(s.build_id, s)
  }
  const rankByBuild = useLoadoutRanks(
    // updated_at, not computed_at: the latter only records when the snapshot
    // ROW was created, so a re-optimize left this signature unchanged and the
    // rank map kept serving its cached (pre-optimize) answer.
    (summaries ?? [])
      .map((s) => `${s.build_id}:${s.updated_at ?? s.computed_at ?? ""}`)
      .sort()
      .join("|"),
  )
  const { user } = useAuth()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const queryClient = useQueryClient()

  const deleteMutation = useMutation({
    mutationFn: (buildId: string) => BuildsService.deleteBuild({ buildId }),
    onSuccess: (_data, buildId) => {
      queryClient.invalidateQueries({ queryKey: ["builds"] })
      queryClient.removeQueries({ queryKey: ["snapshot", buildId] })
      const name = data.data?.find((b) => b.id === buildId)?.name ?? "Build"
      showSuccessToast(`"${name}" deleted.`)
    },
    onError: handleError.bind(showErrorToast),
  })

  const renameMutation = useMutation({
    mutationFn: ({ buildId, name }: { buildId: string; name: string }) =>
      BuildsService.updateBuild({ buildId, requestBody: { name } }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["builds"] }),
    onError: handleError.bind(showErrorToast),
  })

  const changeCharacterMutation = useMutation({
    mutationFn: ({
      buildId,
      character,
    }: {
      buildId: string
      character: string
    }) => BuildsService.updateBuild({ buildId, requestBody: { character } }),
    onSuccess: (_data, { buildId }) => {
      queryClient.invalidateQueries({ queryKey: ["builds"] })
      // Character is part of build_hash — the server dropped the snapshot.
      queryClient.invalidateQueries({ queryKey: ["snapshot", buildId] })
    },
    onError: handleError.bind(showErrorToast),
  })

  const duplicateMutation = useMutation({
    mutationFn: (buildId: string) => BuildsService.cloneBuild({ buildId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["builds"] })
      showSuccessToast("Build duplicated.")
    },
    onError: handleError.bind(showErrorToast),
  })

  const toggleFeaturedMutation = useMutation({
    mutationFn: (buildId: string) => BuildsService.toggleFeatured({ buildId }),
    onSuccess: (_data, buildId) => {
      queryClient.invalidateQueries({ queryKey: ["builds"] })
      queryClient.invalidateQueries({ queryKey: ["builds", "featured"] })
      const build = data.data?.find((b) => b.id === buildId)
      const action = build?.is_featured ? "unfeatured" : "featured"
      showSuccessToast(`Build ${action}.`)
    },
    onError: handleError.bind(showErrorToast),
  })

  const dismissChangeMutation = useMutation({
    mutationFn: (buildId: string) =>
      OptimizeService.markChangeReviewed({ buildId }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["build-summaries"] }),
    onError: handleError.bind(showErrorToast),
  })

  if (!data.data?.length) {
    return (
      <EmptyState icon={Layers} title="No builds yet">
        A build is what you <em>want</em> — e.g. fire damage and survivability.
        Create one above and the optimizer finds the best relics you own to
        match.
      </EmptyState>
    )
  }

  return (
    <div className="space-y-4">
      <ChangesSinceLastSave
        summaries={summaries ?? []}
        buildName={(id) => data.data?.find((b) => b.id === id)?.name ?? "Build"}
        onDismiss={(id) => dismissChangeMutation.mutate(id)}
      />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {data.data.map((build) => (
          <BuildCard
            key={build.id}
            build={{
              id: build.id,
              name: build.name,
              character: build.character,
              groups: (build as any).groups,
              required_effects: (build as any).required_effects,
              updated_at: build.updated_at,
              is_featured: build.is_featured,
            }}
            onDelete={(id) => deleteMutation.mutate(id)}
            onRename={(id, name) =>
              renameMutation.mutate({ buildId: id, name })
            }
            onChangeCharacter={(id, character) =>
              changeCharacterMutation.mutate({ buildId: id, character })
            }
            onDuplicate={(id) => duplicateMutation.mutate(id)}
            onToggleFeatured={
              user?.is_superuser
                ? (id) => toggleFeaturedMutation.mutate(id)
                : undefined
            }
            isDeleting={
              deleteMutation.isPending && deleteMutation.variables === build.id
            }
            summary={summaryByBuild.get(build.id)}
            loadoutRank={rankByBuild.get(build.id)}
          />
        ))}
      </div>
    </div>
  )
}

function AuthBuildsSection() {
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const queryClient = useQueryClient()

  const createMutation = useMutation({
    mutationFn: (data: NewBuildForm) =>
      BuildsService.createBuild({
        requestBody: {
          ...data,
          groups: DEFAULT_GROUPS.map((g) => ({ ...g })),
        },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["builds"] })
      showSuccessToast("Build created.")
    },
    onError: handleError.bind(showErrorToast),
  })

  return (
    <>
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-semibold">Your Builds</h2>
          <p className="text-muted-foreground mt-1">
            Create build definitions to drive the optimizer.
          </p>
        </div>
        <NewBuildDialogContent
          onCreate={(data) => createMutation.mutate(data)}
          isPending={createMutation.isPending}
        />
      </div>
      <Suspense fallback={<Skeleton className="h-48 w-full" />}>
        <AuthBuildList />
      </Suspense>
    </>
  )
}

// --- Anonymous build section (localStorage-backed) ---

function AnonBuildsSection() {
  const { builds, create, remove, update, duplicate } = useLocalBuilds()
  const { showSuccessToast } = useCustomToast()

  function handleDuplicate(id: string) {
    duplicate(id)
    showSuccessToast("Build duplicated.")
  }

  return (
    <>
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-semibold">Your Builds</h2>
          <p className="text-muted-foreground mt-1">
            Create build definitions to drive the optimizer.
          </p>
        </div>
        <NewBuildDialogContent onCreate={create} />
      </div>

      <p className="text-xs text-muted-foreground border rounded-md px-3 py-2 bg-muted/40">
        Builds are stored in your browser.{" "}
        <Link
          to="/login"
          search={{ redirect: "/builds" }}
          className="underline"
        >
          Sign in
        </Link>{" "}
        to sync across devices.
      </p>

      {builds.length === 0 ? (
        <EmptyState icon={Layers} title="No builds yet">
          A build is what you <em>want</em> — e.g. fire damage and
          survivability. Create one above and the optimizer finds the best
          relics you own to match.
        </EmptyState>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {builds.map((build: LocalBuild) => (
            <BuildCard
              key={build.id}
              build={build}
              onDelete={remove}
              onRename={(id, name) => update(id, { name })}
              onChangeCharacter={(id, character) => update(id, { character })}
              onDuplicate={handleDuplicate}
            />
          ))}
        </div>
      )}
    </>
  )
}

// --- Page ---

function BuildsPage() {
  const hasBuildEditor = useRouterState({
    select: (s) =>
      s.matches.some((m) => m.routeId === "/_layout/builds/$buildId"),
  })

  if (hasBuildEditor) return <Outlet />

  return (
    <div className="space-y-6">
      <SuggestedBuildsSection />
      <Separator />
      {isLoggedIn() ? <AuthBuildsSection /> : <AnonBuildsSection />}
    </div>
  )
}
