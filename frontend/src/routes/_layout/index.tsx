import { createFileRoute, Link } from "@tanstack/react-router"
import { Layers, Package, Upload, Zap } from "lucide-react"
import { useSuspenseQuery } from "@tanstack/react-query"

import useAuth from "@/hooks/useAuth"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { BuildsService, SavesService } from "@/client"

export const Route = createFileRoute("/_layout/")({
  component: Dashboard,
  head: () => ({
    meta: [{ title: "Dashboard - Nightreign Relic Planner" }],
  }),
})

function Dashboard() {
  const { user: currentUser } = useAuth()

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">
          Hi, {currentUser?.full_name || currentUser?.email}
        </h1>
        <p className="text-muted-foreground mt-1">
          Welcome to the Nightreign Relic Planner.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <QuickCard
          icon={<Upload className="h-5 w-5" />}
          title="Upload Save"
          description="Import your .sl2 or memory.dat save file to load your relic inventory."
          href="/upload"
          action="Upload"
        />
        <QuickCard
          icon={<Package className="h-5 w-5" />}
          title="Inventory"
          description="Browse and filter the relics from your save file."
          href="/inventory"
          action="View"
        />
        <QuickCard
          icon={<Layers className="h-5 w-5" />}
          title="Builds"
          description="Create and manage build definitions with tiered effect priorities."
          href="/builds"
          action="Manage"
        />
        <QuickCard
          icon={<Zap className="h-5 w-5" />}
          title="Optimize"
          description="Run the vessel optimizer to find the best relic assignments."
          href="/optimize"
          action="Optimize"
        />
      </div>
    </div>
  )
}

interface QuickCardProps {
  icon: React.ReactNode
  title: string
  description: string
  href: string
  action: string
}

function QuickCard({ icon, title, description, href, action }: QuickCardProps) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground">{icon}</span>
          <CardTitle className="text-base">{title}</CardTitle>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <CardDescription>{description}</CardDescription>
        <Button asChild variant="outline" size="sm" className="w-full">
          <Link to={href}>{action}</Link>
        </Button>
      </CardContent>
    </Card>
  )
}
