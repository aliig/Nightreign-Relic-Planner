import { createFileRoute } from "@tanstack/react-router"
import { useSuspenseQuery } from "@tanstack/react-query"
import { Suspense, useMemo, useState } from "react"

import { GameService, SavesService } from "@/client"
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

const EMPTY_EFFECT = 4294967295

function effectPills(effectIds: number[], isDebuff: boolean, effectMap: Map<number, string>) {
  const pills: string[] = []
  for (const id of effectIds) {
    if (id === 0 || id === EMPTY_EFFECT) continue
    const name = effectMap.get(id)
    if (name) pills.push(name)
  }
  if (pills.length === 0) return null
  return (
    <div className="flex flex-wrap gap-1">
      {pills.map((name) => (
        <span
          key={name}
          className={`inline-block rounded px-1.5 py-0.5 text-xs ${
            isDebuff
              ? "bg-destructive/10 text-destructive"
              : "bg-muted text-muted-foreground"
          }`}
        >
          {name}
        </span>
      ))}
    </div>
  )
}

function RelicNameCell({ name, color, tier, isDeep }: {
  name: string; color: string; tier: string; isDeep: boolean
}) {
  const hex = isDeep ? "#8B6FC0" : (COLOR_HEX[color] ?? "#AAAAAA")
  return (
    <div>
      <span className="font-medium" style={{ color: hex }}>
        {name}
      </span>
      <div className="text-xs text-muted-foreground mt-0.5">
        {tier} · {color} · {isDeep ? "Deep" : "Standard"}
      </div>
    </div>
  )
}

// --- Authenticated inventory table ---

function InventoryTable({
  characterId,
  search,
  colorFilter,
  tierFilter,
  deepFilter,
  effectMap,
}: {
  characterId: string
  search: string
  colorFilter: string
  tierFilter: string
  deepFilter: string
  effectMap: Map<number, string>
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
          <TableHead>Relic</TableHead>
          <TableHead>Effects</TableHead>
          <TableHead>Curses</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {relics.map((relic) => (
          <TableRow key={relic.id}>
            <TableCell className="min-w-[180px]">
              <RelicNameCell
                name={relic.name}
                color={relic.color}
                tier={relic.tier}
                isDeep={relic.is_deep}
              />
            </TableCell>
            <TableCell>
              {effectPills([relic.effect_1, relic.effect_2, relic.effect_3], false, effectMap) ?? (
                <span className="text-xs text-muted-foreground italic">—</span>
              )}
            </TableCell>
            <TableCell>
              {effectPills([relic.curse_1, relic.curse_2, relic.curse_3], true, effectMap) ?? (
                <span className="text-xs text-muted-foreground italic">—</span>
              )}
            </TableCell>
          </TableRow>
        ))}
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
  const { data: effectsData } = useSuspenseQuery({
    queryKey: ["game", "effects"],
    queryFn: () => GameService.getEffects(),
    staleTime: Infinity,
  })

  const effectMap = useMemo(() => {
    const m = new Map<number, string>()
    for (const e of effectsData ?? []) {
      if (typeof e.id === "number" && typeof e.name === "string") {
        m.set(e.id, e.name)
      }
    }
    return m
  }, [effectsData])

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
              effectMap={effectMap}
            />
          </Suspense>
        </>
      )}
    </div>
  )
}

// --- Anonymous inventory ---

function AnonInventory() {
  const { data: effectsData } = useSuspenseQuery({
    queryKey: ["game", "effects"],
    queryFn: () => GameService.getEffects(),
    staleTime: Infinity,
  })

  const effectMap = useMemo(() => {
    const m = new Map<number, string>()
    for (const e of effectsData ?? []) {
      if (typeof e.id === "number" && typeof e.name === "string") {
        m.set(e.id, e.name)
      }
    }
    return m
  }, [effectsData])

  const allChars: Array<Record<string, unknown>> = JSON.parse(
    sessionStorage.getItem("parsedCharacters") ?? "[]"
  )

  const defaultChar = (() => {
    try { return JSON.parse(sessionStorage.getItem("selectedCharacter") ?? "null") } catch { return null }
  })()

  const defaultSlot = defaultChar?.slot_index ?? allChars[0]?.slot_index ?? null
  const [selectedSlot, setSelectedSlot] = useState<number | null>(defaultSlot)

  if (allChars.length === 0) {
    return (
      <p className="text-muted-foreground py-8 text-center">
        No inventory loaded. <a href="/upload" className="underline">Upload a save file</a> first.
      </p>
    )
  }

  const char = allChars.find((c) => c.slot_index === selectedSlot) ?? allChars[0]
  const relics: Array<Record<string, unknown>> = (char?.relics as Array<Record<string, unknown>>) ?? []

  const handleCharChange = (slotStr: string) => {
    const slot = Number(slotStr)
    setSelectedSlot(slot)
    const picked = allChars.find((c) => c.slot_index === slot)
    if (picked) sessionStorage.setItem("selectedCharacter", JSON.stringify(picked))
  }

  return (
    <div>
      <div className="flex flex-wrap gap-3 items-center mb-4">
        {allChars.length > 1 && (
          <Select value={String(char?.slot_index ?? "")} onValueChange={handleCharChange}>
            <SelectTrigger className="w-48">
              <SelectValue placeholder="Select character" />
            </SelectTrigger>
            <SelectContent>
              {allChars.map((c) => (
                <SelectItem key={c.slot_index as number} value={String(c.slot_index)}>
                  {c.name as string} (Slot {c.slot_index as number})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <p className="text-sm text-muted-foreground">
          {allChars.length === 1 && <><strong>{char?.name as string}</strong> · </>}
          Session only —{" "}
          <a href="/login" className="underline">sign in</a> to save.
        </p>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Relic</TableHead>
            <TableHead>Effects</TableHead>
            <TableHead>Curses</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {relics.map((relic, i) => (
            <TableRow key={i}>
              <TableCell className="min-w-[180px]">
                <RelicNameCell
                  name={relic.name as string}
                  color={relic.color as string}
                  tier={relic.tier as string}
                  isDeep={relic.is_deep as boolean}
                />
              </TableCell>
              <TableCell>
                {effectPills(
                  [relic.effect_1 as number, relic.effect_2 as number, relic.effect_3 as number],
                  false,
                  effectMap,
                ) ?? <span className="text-xs text-muted-foreground italic">—</span>}
              </TableCell>
              <TableCell>
                {effectPills(
                  [relic.curse_1 as number, relic.curse_2 as number, relic.curse_3 as number],
                  true,
                  effectMap,
                ) ?? <span className="text-xs text-muted-foreground italic">—</span>}
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
