import { useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import { Suspense, useEffect } from "react"

import { BuildsService } from "@/client"
import { Skeleton } from "@/components/ui/skeleton"
import { isLoggedIn } from "@/hooks/useAuth"
import { useLocalBuilds } from "@/hooks/useLocalBuilds"

export const Route = createFileRoute("/_layout/optimize")({
  component: OptimizeRedirectPage,
  head: () => ({
    meta: [{ title: "Optimize - Nightreign Relic Planner" }],
  }),
})

function AuthRedirect() {
  const navigate = useNavigate()
  const { data } = useSuspenseQuery({
    queryKey: ["builds"],
    queryFn: () => BuildsService.listBuilds(),
  })

  const builds = data?.data ?? []

  useEffect(() => {
    if (builds.length > 0) {
      // Pick the most recently updated build
      const sorted = [...builds].sort(
        (a, b) =>
          new Date(b.updated_at ?? 0).getTime() -
          new Date(a.updated_at ?? 0).getTime(),
      )
      navigate({
        to: "/builds/$buildId/optimize",
        params: { buildId: sorted[0].id },
        replace: true,
      })
    }
  }, [builds, navigate])

  if (builds.length === 0) {
    return (
      <p className="text-sm text-muted-foreground py-8">
        No builds found.{" "}
        <Link to="/builds" className="underline">
          Create a build
        </Link>{" "}
        first, then use the Optimize tab on the build page.
      </p>
    )
  }

  return <Skeleton className="h-32 w-full" />
}

function AnonRedirect() {
  const navigate = useNavigate()
  const { builds } = useLocalBuilds()

  useEffect(() => {
    if (builds.length > 0) {
      const sorted = [...builds].sort(
        (a, b) =>
          new Date(b.updated_at ?? 0).getTime() -
          new Date(a.updated_at ?? 0).getTime(),
      )
      navigate({
        to: "/builds/$buildId/optimize",
        params: { buildId: sorted[0].id },
        replace: true,
      })
    }
  }, [builds, navigate])

  if (builds.length === 0) {
    return (
      <p className="text-sm text-muted-foreground py-8">
        No builds found.{" "}
        <Link to="/builds" className="underline">
          Create a build
        </Link>{" "}
        first, then use the Optimize tab on the build page.
      </p>
    )
  }

  return <Skeleton className="h-32 w-full" />
}

function OptimizeRedirectPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Optimize</h1>
        <p className="text-muted-foreground mt-1">
          Redirecting to your most recent build…
        </p>
      </div>
      <Suspense fallback={<Skeleton className="h-32 w-full" />}>
        {isLoggedIn() ? <AuthRedirect /> : <AnonRedirect />}
      </Suspense>
    </div>
  )
}
