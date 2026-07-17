import {
  BookMarked,
  Home,
  Layers,
  Package,
  Sparkles,
  Upload,
  Users,
} from "lucide-react"

import { Logo } from "@/components/Common/Logo"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
} from "@/components/ui/sidebar"
import useAuth from "@/hooks/useAuth"
import { type Item, Main } from "./Main"
import { User } from "./User"

const baseItems: Item[] = [
  {
    icon: Home,
    title: "Dashboard",
    path: "/",
    description: "Your save overview & next steps",
  },
  {
    icon: Upload,
    title: "Upload",
    path: "/upload",
    description: "Import a save file (.sl2 / memory.dat)",
  },
  {
    icon: Package,
    title: "Inventory",
    path: "/inventory",
    description: "Browse, filter & cull your relics",
  },
  {
    icon: Sparkles,
    title: "Relic Rites",
    path: "/rites",
    description: "Bulk-buy & keep only build-worthy relics",
  },
  {
    icon: BookMarked,
    title: "Game Loadouts",
    path: "/loadouts",
    description: "Relic sets saved in your game",
  },
  {
    icon: Layers,
    title: "Optimizer",
    path: "/builds",
    description: "Find the best relics for what you want",
  },
]

export function AppSidebar() {
  const { user: currentUser } = useAuth()

  const items = currentUser?.is_superuser
    ? [...baseItems, { icon: Users, title: "Admin", path: "/admin" }]
    : baseItems

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="px-4 py-6 group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:items-center">
        <Logo variant="responsive" />
      </SidebarHeader>
      <SidebarContent>
        <Main items={items} />
      </SidebarContent>
      <SidebarFooter>
        <User user={currentUser} />
      </SidebarFooter>
    </Sidebar>
  )
}

export default AppSidebar
