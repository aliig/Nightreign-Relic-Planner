import { useMutation, useQueryClient, useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute, redirect } from "@tanstack/react-router"
import { Star } from "lucide-react"
import { Suspense } from "react"

import { type UserPublic, BuildsService, UsersService } from "@/client"
import AddUser from "@/components/Admin/AddUser"
import { columns, type UserTableData } from "@/components/Admin/columns"
import { DataTable } from "@/components/Common/DataTable"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import PendingUsers from "@/components/Pending/PendingUsers"
import useAuth from "@/hooks/useAuth"
import { isLoggedIn } from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

function getUsersQueryOptions() {
  return {
    queryFn: () => UsersService.readUsers({ skip: 0, limit: 100 }),
    queryKey: ["users"],
  }
}

export const Route = createFileRoute("/_layout/admin")({
  component: Admin,
  beforeLoad: async () => {
    if (!isLoggedIn()) {
      throw redirect({ to: "/" })
    }
    const user = await UsersService.readUserMe()
    if (!user.is_superuser) {
      throw redirect({
        to: "/",
      })
    }
  },
  head: () => ({
    meta: [
      {
        title: "Admin - Nightreign Relic Planner",
      },
    ],
  }),
})

function UsersTableContent() {
  const { user: currentUser } = useAuth()
  const { data: users } = useSuspenseQuery(getUsersQueryOptions())

  const tableData: UserTableData[] = users.data.map((user: UserPublic) => ({
    ...user,
    isCurrentUser: currentUser?.id === user.id,
  }))

  return <DataTable columns={columns} data={tableData} />
}

function UsersTable() {
  return (
    <Suspense fallback={<PendingUsers />}>
      <UsersTableContent />
    </Suspense>
  )
}

function FeaturedBuildsContent() {
  const { data } = useSuspenseQuery({
    queryKey: ["builds", "featured"],
    queryFn: () => BuildsService.listFeaturedBuilds(),
  })
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const toggleMutation = useMutation({
    mutationFn: (buildId: string) => BuildsService.toggleFeatured({ buildId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["builds", "featured"] })
      queryClient.invalidateQueries({ queryKey: ["builds"] })
      showSuccessToast("Build unfeatured.")
    },
    onError: handleError.bind(showErrorToast),
  })

  if (!data.data?.length) {
    return (
      <p className="text-sm text-muted-foreground">
        No featured builds. Star a build from your{" "}
        <a href="/builds" className="underline">Builds page</a> to feature it.
      </p>
    )
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {data.data.map((build) => (
        <Card key={build.id}>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between gap-2">
              <CardTitle className="text-base truncate">{build.name}</CardTitle>
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 text-gold shrink-0"
                onClick={() => toggleMutation.mutate(build.id)}
                disabled={toggleMutation.isPending}
                title="Unfeature build"
              >
                <Star className="h-4 w-4 fill-current" />
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">
              {build.character}
              {build.owner_name ? ` · by ${build.owner_name}` : ""}
            </p>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

function FeaturedBuildsManagement() {
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-xl font-bold tracking-tight">Featured Builds</h2>
        <p className="text-muted-foreground">
          Manage which builds appear as suggestions for all users.
        </p>
      </div>
      <Suspense fallback={<Skeleton className="h-32 w-full" />}>
        <FeaturedBuildsContent />
      </Suspense>
    </div>
  )
}

function Admin() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Users</h1>
          <p className="text-muted-foreground">
            Manage user accounts and permissions
          </p>
        </div>
        <AddUser />
      </div>
      <UsersTable />
      <Separator />
      <FeaturedBuildsManagement />
    </div>
  )
}
