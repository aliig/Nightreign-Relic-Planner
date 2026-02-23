import { createFileRoute, Link, Outlet, useRouterState } from "@tanstack/react-router"
import { useMutation, useQueryClient, useSuspenseQuery } from "@tanstack/react-query"
import { Suspense, useState } from "react"
import { Copy, Pencil, Plus, Star, Trash2 } from "lucide-react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"

import { type FeaturedBuildPublic, BuildsService } from "@/client"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
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
import { useLocalBuilds, type LocalBuild } from "@/hooks/useLocalBuilds"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/builds")({
  component: BuildsPage,
  head: () => ({
    meta: [{ title: "Builds - Nightreign Relic Planner" }],
  }),
})

const CHARACTER_NAMES = [
  "Wylder", "Guardian", "Ironeye", "Duchess", "Raider",
  "Revenant", "Recluse", "Executor", "Scholar", "Undertaker",
]

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
          <form onSubmit={form.handleSubmit(handleSubmit)} className="space-y-4">
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
                        <SelectItem key={c} value={c}>{c}</SelectItem>
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

// --- Delete confirmation dialog (shared) ---

interface DeleteDialogProps {
  buildId: string
  buildName: string
  onDelete: (id: string) => void
  isPending?: boolean
}

function DeleteBuildButton({ buildId, buildName, onDelete, isPending }: DeleteDialogProps) {
  const [open, setOpen] = useState(false)

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive hover:text-destructive">
          <Trash2 className="h-4 w-4" />
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete "{buildName}"?</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">This action cannot be undone.</p>
        <div className="flex justify-end gap-2 mt-4">
          <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
          <Button
            variant="destructive"
            onClick={() => { onDelete(buildId); setOpen(false) }}
            disabled={isPending}
          >
            {isPending ? "Deleting…" : "Delete"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

// --- Shared build card renderer ---

interface BuildCardData {
  id: string
  name: string
  character: string
  tiers: Record<string, number[]>
  updated_at?: string | null
  is_featured?: boolean
}

function BuildCard({
  build,
  onDelete,
  onRename,
  onDuplicate,
  onToggleFeatured,
  isDeleting,
}: {
  build: BuildCardData
  onDelete: (id: string) => void
  onRename: (id: string, newName: string) => void
  onDuplicate?: (id: string) => void
  onToggleFeatured?: (id: string) => void
  isDeleting?: boolean
}) {
  const [draftName, setDraftName] = useState(build.name)
  const effectCount = Object.values(build.tiers).reduce((acc, ids) => acc + ids.length, 0)

  function commitRename() {
    const trimmed = draftName.trim()
    if (trimmed && trimmed !== build.name) {
      onRename(build.id, trimmed)
    } else {
      setDraftName(build.name)
    }
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2">
          <input
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); e.currentTarget.blur() }
              if (e.key === "Escape") { setDraftName(build.name); e.currentTarget.blur() }
            }}
            onBlur={commitRename}
            className="text-base font-semibold bg-transparent border-b border-transparent hover:border-muted-foreground/30 focus:border-primary focus:outline-none focus:ring-0 py-0.5 min-w-0 flex-1 truncate transition-colors"
          />
          <div className="flex items-center gap-1 shrink-0">
            {onToggleFeatured && (
              <Button
                variant="ghost"
                size="icon"
                className={`h-8 w-8 ${build.is_featured ? "text-yellow-500" : "text-muted-foreground"}`}
                onClick={() => onToggleFeatured(build.id)}
                title={build.is_featured ? "Unfeature build" : "Feature build"}
              >
                <Star className={`h-4 w-4 ${build.is_featured ? "fill-current" : ""}`} />
              </Button>
            )}
            {onDuplicate && (
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                onClick={() => onDuplicate(build.id)}
                title="Duplicate build"
              >
                <Copy className="h-4 w-4" />
              </Button>
            )}
            <Button asChild variant="ghost" size="icon" className="h-8 w-8" title="Edit build">
              <Link to="/builds/$buildId" params={{ buildId: build.id }}>
                <Pencil className="h-4 w-4" />
              </Link>
            </Button>
            <DeleteBuildButton
              buildId={build.id}
              buildName={build.name}
              onDelete={onDelete}
              isPending={isDeleting}
            />
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <CardDescription>
          {build.character} · {effectCount} effect{effectCount !== 1 ? "s" : ""} prioritized
        </CardDescription>
        {build.updated_at && (
          <p className="text-xs text-muted-foreground mt-1">
            Updated {new Date(build.updated_at).toLocaleDateString()}
          </p>
        )}
      </CardContent>
    </Card>
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
  const effectCount = Object.values(build.tiers).reduce((acc, ids) => acc + ids.length, 0)

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base truncate">{build.name}</CardTitle>
          <div className="flex items-center gap-1 shrink-0">
            {isSuperuser && onToggleFeatured && (
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 text-yellow-500"
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
      <CardContent>
        <CardDescription>
          {build.character} · {effectCount} effect{effectCount !== 1 ? "s" : ""} prioritized
        </CardDescription>
        {build.owner_name && (
          <p className="text-xs text-muted-foreground mt-1">by {build.owner_name}</p>
        )}
      </CardContent>
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
      createFull({
        name: build.name,
        character: build.character,
        tiers: build.tiers,
        family_tiers: build.family_tiers as Record<string, unknown>,
        include_deep: build.include_deep,
        curse_max: build.curse_max,
      })
      showSuccessToast("Build saved to your browser.")
    }
  }

  if (!data.data?.length) return null

  return (
    <div className="space-y-3">
      <div>
        <h2 className="text-lg font-semibold">Suggested Builds</h2>
        <p className="text-sm text-muted-foreground">
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

function AuthBuildList() {
  const { data } = useSuspenseQuery({
    queryKey: ["builds"],
    queryFn: () => BuildsService.listBuilds(),
  })
  const { user } = useAuth()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const queryClient = useQueryClient()

  const deleteMutation = useMutation({
    mutationFn: (buildId: string) => BuildsService.deleteBuild({ buildId }),
    onSuccess: (_data, buildId) => {
      queryClient.invalidateQueries({ queryKey: ["builds"] })
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

  if (!data.data?.length) {
    return (
      <p className="text-muted-foreground py-8 text-center">
        No builds yet. Create one to get started.
      </p>
    )
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {data.data.map((build) => (
        <BuildCard
          key={build.id}
          build={{
            id: build.id,
            name: build.name,
            character: build.character,
            tiers: build.tiers as Record<string, number[]>,
            updated_at: build.updated_at,
            is_featured: build.is_featured,
          }}
          onDelete={(id) => deleteMutation.mutate(id)}
          onRename={(id, name) => renameMutation.mutate({ buildId: id, name })}
          onDuplicate={(id) => duplicateMutation.mutate(id)}
          onToggleFeatured={user?.is_superuser ? (id) => toggleFeaturedMutation.mutate(id) : undefined}
          isDeleting={deleteMutation.isPending && deleteMutation.variables === build.id}
        />
      ))}
    </div>
  )
}

function AuthBuildsSection() {
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const queryClient = useQueryClient()

  const createMutation = useMutation({
    mutationFn: (data: NewBuildForm) => BuildsService.createBuild({ requestBody: data }),
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
          <h1 className="text-2xl font-semibold">Builds</h1>
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
          <h1 className="text-2xl font-semibold">Builds</h1>
          <p className="text-muted-foreground mt-1">
            Create build definitions to drive the optimizer.
          </p>
        </div>
        <NewBuildDialogContent onCreate={create} />
      </div>

      <p className="text-xs text-muted-foreground border rounded-md px-3 py-2 bg-muted/40">
        Builds are stored in your browser.{" "}
        <Link to="/login" search={{ redirect: "/builds" }} className="underline">
          Sign in
        </Link>{" "}
        to sync across devices.
      </p>

      {builds.length === 0 ? (
        <p className="text-muted-foreground py-8 text-center">
          No builds yet. Create one to get started.
        </p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {builds.map((build: LocalBuild) => (
            <BuildCard
              key={build.id}
              build={build}
              onDelete={remove}
              onRename={(id, name) => update(id, { name })}
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
    select: (s) => s.matches.some((m) => m.routeId === "/_layout/builds/$buildId"),
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
