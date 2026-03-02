import { createFileRoute, Link, Outlet, useParams, useRouterState } from "@tanstack/react-router"
import { useSuspenseQuery } from "@tanstack/react-query"
import { Suspense } from "react"
import { Pencil, Zap } from "lucide-react"

import { BuildsService } from "@/client"
import { cn } from "@/lib/utils"
import { isLoggedIn } from "@/hooks/useAuth"
import { useLocalBuilds } from "@/hooks/useLocalBuilds"

export const Route = createFileRoute("/_layout/builds/$buildId")({
  component: BuildDetailLayout,
})

function AuthBuildName({ buildId }: { buildId: string }) {
  const { data } = useSuspenseQuery({
    queryKey: ["builds", buildId],
    queryFn: () => BuildsService.getBuild({ buildId }),
  })
  return <>{(data as any).name ?? "Build"}</>
}

function AnonBuildName({ buildId }: { buildId: string }) {
  const { getById } = useLocalBuilds()
  const build = getById(buildId)
  return <>{build?.name ?? "Build"}</>
}

function BuildDetailLayout() {
  const { buildId } = useParams({ from: "/_layout/builds/$buildId" })

  const isOptimizeTab = useRouterState({
    select: (s) => s.location.pathname.endsWith("/optimize"),
  })

  return (
    <div className="space-y-4">
      {/* Breadcrumb */}
      <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
        <Link to="/builds" className="hover:text-foreground transition-colors">
          Builds
        </Link>
        <span>/</span>
        <span className="text-foreground font-medium truncate max-w-[200px]">
          <Suspense fallback="…">
            {isLoggedIn()
              ? <AuthBuildName buildId={buildId} />
              : <AnonBuildName buildId={buildId} />
            }
          </Suspense>
        </span>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b">
        <Link
          to="/builds/$buildId/edit"
          params={{ buildId }}
          className={cn(
            "flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 transition-colors -mb-px",
            !isOptimizeTab
              ? "border-primary text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground hover:border-muted-foreground/30",
          )}
        >
          <Pencil className="h-3.5 w-3.5" />
          Edit
        </Link>
        <Link
          to="/builds/$buildId/optimize"
          params={{ buildId }}
          className={cn(
            "flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 transition-colors -mb-px",
            isOptimizeTab
              ? "border-primary text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground hover:border-muted-foreground/30",
          )}
        >
          <Zap className="h-3.5 w-3.5" />
          Optimize
        </Link>
      </div>

      {/* Active tab content */}
      <Outlet />
    </div>
  )
}
