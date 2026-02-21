import { createFileRoute, Link } from "@tanstack/react-router"
import { useMutation, useQueryClient, useSuspenseQuery } from "@tanstack/react-query"
import { Suspense, useState } from "react"
import { Plus, Pencil, Trash2 } from "lucide-react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"

import { BuildsService } from "@/client"
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
import { Skeleton } from "@/components/ui/skeleton"
import { useCustomToast } from "@/hooks/useCustomToast"
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

function NewBuildDialog({ onCreated }: { onCreated: () => void }) {
  const [open, setOpen] = useState(false)
  const { showSuccessToast } = useCustomToast()
  const queryClient = useQueryClient()

  const form = useForm<NewBuildForm>({
    resolver: zodResolver(newBuildSchema),
    defaultValues: { name: "", character: "Wylder" },
  })

  const createMutation = useMutation({
    mutationFn: (data: NewBuildForm) =>
      BuildsService.createBuild({ requestBody: data }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["builds"] })
      showSuccessToast("Build created.")
      form.reset()
      setOpen(false)
      onCreated()
    },
    onError: handleError,
  })

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
            onSubmit={form.handleSubmit((d) => createMutation.mutate(d))}
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
                        <SelectItem key={c} value={c}>{c}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />
            <Button type="submit" className="w-full" disabled={createMutation.isPending}>
              {createMutation.isPending ? "Creating…" : "Create"}
            </Button>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

function DeleteBuildButton({ buildId, buildName }: { buildId: string; buildName: string }) {
  const [open, setOpen] = useState(false)
  const { showSuccessToast } = useCustomToast()
  const queryClient = useQueryClient()

  const deleteMutation = useMutation({
    mutationFn: () => BuildsService.deleteBuild({ buildId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["builds"] })
      showSuccessToast(`"${buildName}" deleted.`)
      setOpen(false)
    },
    onError: handleError,
  })

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
            onClick={() => deleteMutation.mutate()}
            disabled={deleteMutation.isPending}
          >
            {deleteMutation.isPending ? "Deleting…" : "Delete"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

function BuildList() {
  const { data } = useSuspenseQuery({
    queryKey: ["builds"],
    queryFn: () => BuildsService.listBuilds(),
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
      {data.data.map((build) => {
        const effectCount = Object.values(build.tiers as Record<string, number[]>)
          .reduce((acc, ids) => acc + ids.length, 0)
        return (
          <Card key={build.id}>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-base truncate">{build.name}</CardTitle>
                <div className="flex items-center gap-1 shrink-0">
                  <Button asChild variant="ghost" size="icon" className="h-8 w-8">
                    <Link to="/builds/$buildId" params={{ buildId: build.id }}>
                      <Pencil className="h-4 w-4" />
                    </Link>
                  </Button>
                  <DeleteBuildButton buildId={build.id} buildName={build.name} />
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
      })}
    </div>
  )
}

function BuildsPage() {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Builds</h1>
          <p className="text-muted-foreground mt-1">
            Create build definitions to drive the optimizer.
          </p>
        </div>
        <NewBuildDialog onCreated={() => {}} />
      </div>

      <Suspense fallback={<Skeleton className="h-48 w-full" />}>
        <BuildList />
      </Suspense>
    </div>
  )
}
