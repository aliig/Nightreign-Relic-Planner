import { createFileRoute, Link } from "@tanstack/react-router"
import { Layers, Package, Upload, Zap } from "lucide-react"

import useAuth from "@/hooks/useAuth"
import { useSaveStatus } from "@/hooks/useSaveStatus"
import { formatRelativeTime } from "@/utils"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"

export const Route = createFileRoute("/_layout/")({
  component: Dashboard,
  head: () => ({
    meta: [{ title: "Dashboard - Nightreign Relic Planner" }],
  }),
})

function Dashboard() {
  const { user: currentUser } = useAuth()
  const { status: saveStatus, isLoading: saveStatusLoading, isAnon } = useSaveStatus()

  let uploadFooter: React.ReactNode = null
  if (!saveStatusLoading && saveStatus) {
    uploadFooter = (
      <div className="text-xs text-muted-foreground space-y-0.5">
        <div className="flex items-center gap-1.5 flex-wrap">
          <Badge variant="secondary" className="text-[10px] px-1.5 py-0">{saveStatus.platform}</Badge>
          <span>{saveStatus.character_count} character{saveStatus.character_count !== 1 ? "s" : ""}</span>
        </div>
        <p>
          {isAnon
            ? "Session data loaded"
            : `Uploaded ${formatRelativeTime(saveStatus.uploaded_at)}`}
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">
          {currentUser
            ? `Hi, ${currentUser.full_name || currentUser.email}`
            : "Welcome to Nightreign Relic Planner"}
        </h1>
        <p className="text-muted-foreground mt-1">
          {currentUser
            ? "Welcome back to your Relic Planner."
            : "Upload a save file to get started, or sign in to sync your data."}
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <QuickCard
          icon={<Upload className="h-5 w-5" />}
          title="Upload Save"
          description="Import your .sl2 or memory.dat save file to load your relic inventory."
          href="/upload"
          action={saveStatus ? "Re-upload" : "Upload"}
          footer={uploadFooter}
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
  footer?: React.ReactNode
}

function QuickCard({ icon, title, description, href, action, footer }: QuickCardProps) {
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
        {footer}
        <Button asChild variant="outline" size="sm" className="w-full">
          <Link to={href}>{action}</Link>
        </Button>
      </CardContent>
    </Card>
  )
}
