import { useQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import {
  BookMarked,
  Check,
  Layers,
  type LucideIcon,
  Package,
  Upload,
} from "lucide-react"

import { BuildsService } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import useAuth, { isLoggedIn } from "@/hooks/useAuth"
import { useLocalBuilds } from "@/hooks/useLocalBuilds"
import { useSaveStatus } from "@/hooks/useSaveStatus"
import { cn } from "@/lib/utils"
import { formatRelativeTime } from "@/utils"

export const Route = createFileRoute("/_layout/")({
  component: Dashboard,
  head: () => ({
    meta: [{ title: "Dashboard - Nightreign Relic Planner" }],
  }),
})

interface StepDef {
  n: number
  icon: LucideIcon
  title: string
  blurb: string
  href: string
  cta: string
  done?: boolean
  locked?: boolean
  footer?: React.ReactNode
}

function Dashboard() {
  const { user: currentUser } = useAuth()
  const {
    status: saveStatus,
    isLoading: saveStatusLoading,
    isAnon,
  } = useSaveStatus()
  const loggedIn = isLoggedIn()

  // "Do you have a build yet?" drives the step-3 call-to-action.
  const { data: authBuilds } = useQuery({
    queryKey: ["builds"],
    queryFn: () => BuildsService.listBuilds(),
    enabled: loggedIn,
    staleTime: 5 * 60 * 1000,
  })
  const { builds: anonBuilds } = useLocalBuilds()
  const hasBuilds = loggedIn
    ? (authBuilds?.data?.length ?? 0) > 0
    : anonBuilds.length > 0

  const saveLoaded = !!saveStatus
  // Recommend the next action: upload first, otherwise head to the optimizer.
  const currentStep = !saveLoaded ? 1 : 3

  const saveMeta =
    !saveStatusLoading && saveStatus ? (
      <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
        <Badge variant="secondary" className="px-1.5 py-0 text-[10px]">
          {saveStatus.platform}
        </Badge>
        <span>
          {saveStatus.profile_count} profile
          {saveStatus.profile_count !== 1 ? "s" : ""}
        </span>
        <span aria-hidden>·</span>
        <span>
          {isAnon
            ? "Session data"
            : `Uploaded ${formatRelativeTime(saveStatus.uploaded_at)}`}
        </span>
      </div>
    ) : null

  const steps: StepDef[] = [
    {
      n: 1,
      icon: Upload,
      title: "Upload your save",
      blurb:
        "Import your .sl2 or memory.dat so the planner can read your relics.",
      href: "/upload",
      cta: saveLoaded ? "Re-upload" : "Upload save",
      done: saveLoaded,
      footer: saveMeta,
    },
    {
      n: 2,
      icon: Package,
      title: "Review your relics",
      blurb:
        "Browse and filter your inventory — find unused or outdated relics worth selling.",
      href: "/inventory",
      cta: "Open inventory",
      locked: !saveLoaded,
    },
    {
      n: 3,
      icon: Layers,
      title: "Optimize a build",
      blurb:
        "Tell the planner what you want; it finds the best relics you own to match.",
      href: "/builds",
      cta: hasBuilds ? "Open optimizer" : "Create a build",
      locked: !saveLoaded,
    },
    {
      n: 4,
      icon: BookMarked,
      title: "Save to your game",
      blurb:
        "Turn an optimized result into a loadout, then export it back to your save file.",
      href: "/loadouts",
      cta: "Open game loadouts",
      locked: !saveLoaded,
    },
  ]

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">
          {currentUser
            ? `Hi, ${currentUser.full_name || currentUser.email}`
            : "Welcome to Nightreign Relic Planner"}
        </h1>
        <p className="mt-1 text-muted-foreground">
          {saveLoaded
            ? "Pick up where you left off, or jump to any step below."
            : "Four steps from save file to an optimized loadout — start by uploading your save."}
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {steps.map((step) => (
          <StepCard
            key={step.n}
            step={step}
            isCurrent={step.n === currentStep}
          />
        ))}
      </div>
    </div>
  )
}

function StepCard({ step, isCurrent }: { step: StepDef; isCurrent: boolean }) {
  const { icon: Icon, n, title, blurb, href, cta, done, locked, footer } = step
  return (
    <Card
      className={cn(
        "relative flex h-full flex-col transition-colors",
        isCurrent && "border-primary bg-primary/5",
        locked && "opacity-60",
      )}
    >
      {isCurrent && (
        <Badge className="absolute left-0 top-0 rounded-br-sm px-2 py-0.5 text-[10px]">
          Start here
        </Badge>
      )}
      <CardHeader className="pb-2">
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "flex size-6 shrink-0 items-center justify-center rounded-full border text-xs font-medium",
              done
                ? "border-primary bg-primary/15 text-primary"
                : "border-border text-muted-foreground",
            )}
          >
            {done ? <Check className="size-3.5" /> : n}
          </span>
          <Icon className="size-4 text-muted-foreground" />
          <CardTitle className="text-base">{title}</CardTitle>
        </div>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-3">
        <CardDescription>{blurb}</CardDescription>
        {footer}
        <div className="mt-auto pt-1">
          {locked ? (
            <Button
              variant="outline"
              size="sm"
              className="w-full"
              disabled
              title="Upload a save first"
            >
              Upload a save first
            </Button>
          ) : (
            <Button
              asChild
              variant={isCurrent ? "default" : "outline"}
              size="sm"
              className="w-full"
            >
              <Link to={href}>{cta}</Link>
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
