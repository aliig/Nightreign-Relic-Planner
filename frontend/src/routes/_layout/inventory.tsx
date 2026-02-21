import { createFileRoute } from "@tanstack/react-router"
import { useSuspenseQuery } from "@tanstack/react-query"
import { Suspense, useMemo, useState } from "react"

import { SavesService } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/inventory")({
  component: InventoryPage,
  head: () => ({
    meta: [{ title: "Inventory - Nightreign Relic Planner" }],
  }),
})

const COLOR_HEX: Record<string, string> = {
  Red: "#FF4444",
  Blue: "#4488FF",
  Yellow: "#B8860B",
  Green: "#44BB44",
  White: "#AAAAAA",
}

function RelicColorBadge({ color }: { color: string }) {
  return (
    <Badge
      variant="outline"
      style={{ borderColor: COLOR_HEX[color] ?? "#888", color: COLOR_HEX[color] ?? "#888" }}
    >
      {color}
    </Badge>
  )
}

function TierBadge({ tier }: { tier: string }) {
  const variant = tier === "Grand" ? "default" : tier === "Polished" ? "secondary" : "outline"
  return <Badge variant={variant}>{tier}</Badge>
}

function InventoryTable({
  characterId,
  search,
  colorFilter,
  tierFilter,
  deepFilter,
}: {
  characterId: string
  search: string
  colorFilter: string
  tierFilter: string
  deepFilter: string
}) {
  const { data } = useSuspenseQuery({
    queryKey: ["relics", characterId],
    queryFn: () => SavesService.getCharacterRelics({ characterId }),
    staleTime: 5 * 60 * 1000,
  })

  const relics = useMemo(() => {
    return (data.data ?? []).filter((r) => {
      if (search && !r.name.toLowerCase().includes(search.toLowerCase())) return false
      if (colorFilter !== "all" && r.color !== colorFilter) return false
      if (tierFilter !== "all" && r.tier !== tierFilter) return false
      if (deepFilter === "deep" && !r.is_deep) return false
      if (deepFilter === "standard" && r.is_deep) return false
      return true
    })
  }, [data.data, search, colorFilter, tierFilter, deepFilter])

  if (relics.length === 0) {
    return <p className="text-sm text-muted-foreground py-8 text-center">No relics match the current filters.</p>
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead>Color</TableHead>
          <TableHead>Tier</TableHead>
          <TableHead>Type</TableHead>
          <TableHead className="text-right">Effects</TableHead>
          <TableHead className="text-right">Curses</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {relics.map((relic) => {
          const effectCount = [relic.effect_1, relic.effect_2, relic.effect_3].filter(
            (e) => e !== 0 && e !== 4294967295,
          ).length
          const curseCount = [relic.curse_1, relic.curse_2, relic.curse_3].filter(
            (e) => e !== 0 && e !== 4294967295,
          ).length
          return (
            <TableRow key={relic.id}>
              <TableCell className="font-medium">{relic.name}</TableCell>
              <TableCell><RelicColorBadge color={relic.color} /></TableCell>
              <TableCell><TierBadge tier={relic.tier} /></TableCell>
              <TableCell>
                <span className="text-xs text-muted-foreground">
                  {relic.is_deep ? "Deep" : "Standard"}
                </span>
              </TableCell>
              <TableCell className="text-right text-sm">{effectCount}</TableCell>
              <TableCell className="text-right text-sm">{curseCount}</TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}

function AuthInventory() {
  const { data: chars } = useSuspenseQuery({
    queryKey: ["characters"],
    queryFn: () => SavesService.listCharacters(),
    staleTime: 5 * 60 * 1000,
  })

  const [selectedId, setSelectedId] = useState<string | null>(
    chars.data?.[0]?.id ?? null,
  )
  const [search, setSearch] = useState("")
  const [colorFilter, setColorFilter] = useState("all")
  const [tierFilter, setTierFilter] = useState("all")
  const [deepFilter, setDeepFilter] = useState("all")

  if (!chars.data?.length) {
    return (
      <p className="text-muted-foreground py-8 text-center">
        No characters found. <a href="/upload" className="underline">Upload a save file</a> first.
      </p>
    )
  }

  return (
    <div className="space-y-4">
      {/* Character selector */}
      <div className="flex flex-wrap gap-3">
        <Select value={selectedId ?? ""} onValueChange={setSelectedId}>
          <SelectTrigger className="w-48">
            <SelectValue placeholder="Select character" />
          </SelectTrigger>
          <SelectContent>
            {chars.data.map((c) => (
              <SelectItem key={c.id} value={c.id}>
                {c.name} (Slot {c.slot_index})
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {selectedId && (
        <>
          {/* Filters */}
          <div className="flex flex-wrap gap-3">
            <Input
              placeholder="Search by name…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-48"
            />
            <Select value={colorFilter} onValueChange={setColorFilter}>
              <SelectTrigger className="w-32">
                <SelectValue placeholder="Color" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Colors</SelectItem>
                {["Red", "Blue", "Yellow", "Green", "White"].map((c) => (
                  <SelectItem key={c} value={c}>{c}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={tierFilter} onValueChange={setTierFilter}>
              <SelectTrigger className="w-36">
                <SelectValue placeholder="Tier" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Tiers</SelectItem>
                {["Grand", "Polished", "Delicate"].map((t) => (
                  <SelectItem key={t} value={t}>{t}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={deepFilter} onValueChange={setDeepFilter}>
              <SelectTrigger className="w-32">
                <SelectValue placeholder="Type" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Types</SelectItem>
                <SelectItem value="standard">Standard</SelectItem>
                <SelectItem value="deep">Deep</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <Suspense fallback={<Skeleton className="h-48 w-full" />}>
            <InventoryTable
              characterId={selectedId}
              search={search}
              colorFilter={colorFilter}
              tierFilter={tierFilter}
              deepFilter={deepFilter}
            />
          </Suspense>
        </>
      )}
    </div>
  )
}

function AnonInventory() {
  const raw = sessionStorage.getItem("selectedCharacter")
  const char = raw ? JSON.parse(raw) : null

  if (!char) {
    return (
      <p className="text-muted-foreground py-8 text-center">
        No inventory loaded. <a href="/upload" className="underline">Upload a save file</a> first.
      </p>
    )
  }

  const relics: Array<Record<string, unknown>> = char.relics ?? []

  return (
    <div>
      <p className="text-sm text-muted-foreground mb-4">
        Showing inventory for <strong>{char.name}</strong> (session only —{" "}
        <a href="/login" className="underline">sign in</a> to save).
      </p>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Color</TableHead>
            <TableHead>Tier</TableHead>
            <TableHead>Type</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {relics.map((relic, i) => (
            <TableRow key={i}>
              <TableCell className="font-medium">{relic.name as string}</TableCell>
              <TableCell><RelicColorBadge color={relic.color as string} /></TableCell>
              <TableCell><TierBadge tier={relic.tier as string} /></TableCell>
              <TableCell>
                <span className="text-xs text-muted-foreground">
                  {relic.is_deep ? "Deep" : "Standard"}
                </span>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function InventoryPage() {
  const { user } = useAuth()

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Relic Inventory</h1>
        <p className="text-muted-foreground mt-1">Browse relics from your save file.</p>
      </div>
      <Suspense fallback={<Skeleton className="h-48 w-full" />}>
        {user ? <AuthInventory /> : <AnonInventory />}
      </Suspense>
    </div>
  )
}
