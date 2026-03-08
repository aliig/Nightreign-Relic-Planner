import { useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { Filter, Search, X } from "lucide-react"
import { Suspense, useMemo, useState } from "react"

import { GameService, SavesService } from "@/client"
import {
  buildEffectMap,
  EffectList,
  RelicNameCell,
} from "@/components/RelicDisplay"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
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

// --- Shared Components & Logic ---

function applyFilters(
  relics: any[],
  search: string,
  colorFilter: string,
  tierFilter: string,
  deepFilter: string,
  effectFilter: number[],
  effectMap: Map<number, string>,
) {
  return relics.filter((r) => {
    if (search && !r.name.toLowerCase().includes(search.toLowerCase()))
      return false
    if (colorFilter !== "all" && r.color !== colorFilter) return false
    if (tierFilter !== "all" && r.tier !== tierFilter) return false
    if (deepFilter === "deep" && !r.is_deep) return false
    if (deepFilter === "standard" && r.is_deep) return false

    if (effectFilter.length > 0) {
      const selectedNames = effectFilter
        .map((id) => effectMap.get(id))
        .filter(Boolean)
      const relicEffectNames = [r.effect_1, r.effect_2, r.effect_3]
        .map((id) => effectMap.get(id as number))
        .filter(Boolean)

      if (!selectedNames.every((name) => relicEffectNames.includes(name))) {
        return false
      }
    }
    return true
  })
}

function EffectMultiSelect({
  effectsData,
  selectedEffects,
  onChange,
}: {
  effectsData: unknown[]
  selectedEffects: number[]
  onChange: (ids: number[]) => void
}) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState("")

  const effects = useMemo(() => {
    const arr = (
      (effectsData as Array<{ id: number; name: string }>) ?? []
    ).filter((e) => typeof e.id === "number" && typeof e.name === "string")
    // Deduplicate by name to avoid showing aliases as separate items in the picker
    const seen = new Set<string>()
    const unique: Array<{ id: number; name: string }> = []
    for (const e of arr) {
      if (!seen.has(e.name)) {
        seen.add(e.name)
        unique.push(e)
      }
    }
    return unique.filter(
      (e) => !search || e.name.toLowerCase().includes(search.toLowerCase()),
    )
  }, [effectsData, search])

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" className="w-48 justify-start">
          <Filter className="mr-2 h-4 w-4" />
          {selectedEffects.length > 0
            ? `${selectedEffects.length} Effect${selectedEffects.length > 1 ? "s" : ""}`
            : "Filter Effects"}
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-md p-0 overflow-hidden">
        <DialogHeader className="p-4 pb-2">
          <DialogTitle>Filter by Effects (AND)</DialogTitle>
        </DialogHeader>
        <div className="px-4 pb-2 relative">
          <Search className="absolute left-6 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search effects..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8"
          />
        </div>
        {selectedEffects.length > 0 && (
          <div className="px-4 pb-2 flex flex-wrap gap-1 max-h-24 overflow-y-auto">
            {selectedEffects.map((id) => {
              const e = (
                effectsData as Array<{ id: number; name: string }>
              ).find((x) => x.id === id)
              if (!e) return null
              return (
                <Badge
                  key={id}
                  variant="secondary"
                  className="text-xs font-normal"
                >
                  {e.name}
                  <button
                    type="button"
                    onClick={() =>
                      onChange(selectedEffects.filter((x) => x !== id))
                    }
                    className="ml-1 hover:text-destructive"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </Badge>
              )
            })}
          </div>
        )}
        <div className="max-h-[300px] overflow-y-auto border-t">
          {effects.length > 0 ? (
            <div className="p-2 flex flex-col gap-1">
              {effects.map((e) => {
                const isSelected = selectedEffects.includes(e.id)
                return (
                  <button
                    key={e.id}
                    type="button"
                    onClick={() => {
                      if (isSelected) {
                        onChange(selectedEffects.filter((id) => id !== e.id))
                      } else {
                        onChange([...selectedEffects, e.id])
                      }
                    }}
                    className={`flex items-center gap-2 px-2 py-1.5 rounded-sm text-sm hover:bg-accent text-left w-full ${isSelected ? "bg-accent/50" : ""}`}
                  >
                    <Checkbox
                      checked={isSelected}
                      tabIndex={-1}
                      className="pointer-events-none"
                    />
                    {e.name}
                  </button>
                )
              })}
            </div>
          ) : (
            <p className="p-4 text-center text-sm text-muted-foreground">
              No effects found.
            </p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

function InventoryFilters({
  search,
  setSearch,
  colorFilter,
  setColorFilter,
  tierFilter,
  setTierFilter,
  deepFilter,
  setDeepFilter,
  effectFilter,
  setEffectFilter,
  effectsData,
}: any) {
  return (
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
          {["Red", "Blue", "Yellow", "Green"].map((c) => (
            <SelectItem key={c} value={c}>
              {c}
            </SelectItem>
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
            <SelectItem key={t} value={t}>
              {t}
            </SelectItem>
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
      <EffectMultiSelect
        effectsData={effectsData}
        selectedEffects={effectFilter}
        onChange={setEffectFilter}
      />
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
  effectFilter,
  effectMap,
}: {
  characterId: string
  search: string
  colorFilter: string
  tierFilter: string
  deepFilter: string
  effectFilter: number[]
  effectMap: Map<number, string>
}) {
  const { data } = useSuspenseQuery({
    queryKey: ["relics", characterId],
    queryFn: () => SavesService.getCharacterRelics({ characterId }),
    staleTime: 5 * 60 * 1000,
  })

  const relics = useMemo(() => {
    return applyFilters(
      data.data ?? [],
      search,
      colorFilter,
      tierFilter,
      deepFilter,
      effectFilter,
      effectMap,
    )
  }, [
    data.data,
    search,
    colorFilter,
    tierFilter,
    deepFilter,
    effectFilter,
    effectMap,
  ])

  if (relics.length === 0) {
    return (
      <p className="text-sm text-muted-foreground py-8 text-center">
        No relics match the current filters.
      </p>
    )
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
              {EffectList({
                effectIds: [relic.effect_1, relic.effect_2, relic.effect_3],
                isCurse: false,
                effectMap,
              }) ?? (
                <span className="text-xs text-muted-foreground italic">—</span>
              )}
            </TableCell>
            <TableCell>
              {EffectList({
                effectIds: [relic.curse_1, relic.curse_2, relic.curse_3],
                isCurse: true,
                effectMap,
              }) ?? (
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

  const effectMap = useMemo(
    () => buildEffectMap((effectsData ?? []) as unknown[]),
    [effectsData],
  )

  const [selectedId, setSelectedId] = useState<string | null>(
    chars.data?.[0]?.id ?? null,
  )
  const [search, setSearch] = useState("")
  const [colorFilter, setColorFilter] = useState("all")
  const [tierFilter, setTierFilter] = useState("all")
  const [deepFilter, setDeepFilter] = useState("all")
  const [effectFilter, setEffectFilter] = useState<number[]>([])

  if (!chars.data?.length) {
    return (
      <p className="text-muted-foreground py-8 text-center">
        No characters found.{" "}
        <a href="/upload" className="underline">
          Upload a save file
        </a>{" "}
        first.
      </p>
    )
  }

  return (
    <div className="space-y-4">
      {/* Character selector — hidden when only one character */}
      <div className="flex flex-wrap gap-3 items-center">
        {chars.data.length > 1 ? (
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
        ) : (
          <p className="text-sm text-muted-foreground">
            <strong>{chars.data[0]?.name}</strong>
          </p>
        )}
      </div>

      {selectedId && (
        <>
          <InventoryFilters
            search={search}
            setSearch={setSearch}
            colorFilter={colorFilter}
            setColorFilter={setColorFilter}
            tierFilter={tierFilter}
            setTierFilter={setTierFilter}
            deepFilter={deepFilter}
            setDeepFilter={setDeepFilter}
            effectFilter={effectFilter}
            setEffectFilter={setEffectFilter}
            effectsData={effectsData}
          />

          <Suspense fallback={<Skeleton className="h-48 w-full" />}>
            <InventoryTable
              characterId={selectedId}
              search={search}
              colorFilter={colorFilter}
              tierFilter={tierFilter}
              deepFilter={deepFilter}
              effectFilter={effectFilter}
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

  const effectMap = useMemo(
    () => buildEffectMap((effectsData ?? []) as unknown[]),
    [effectsData],
  )

  const allChars: Array<Record<string, unknown>> = JSON.parse(
    sessionStorage.getItem("parsedCharacters") ?? "[]",
  )

  const defaultChar = (() => {
    try {
      return JSON.parse(sessionStorage.getItem("selectedCharacter") ?? "null")
    } catch {
      return null
    }
  })()

  const defaultSlot = defaultChar?.slot_index ?? allChars[0]?.slot_index ?? null
  const [selectedSlot, setSelectedSlot] = useState<number | null>(defaultSlot)

  const [search, setSearch] = useState("")
  const [colorFilter, setColorFilter] = useState("all")
  const [tierFilter, setTierFilter] = useState("all")
  const [deepFilter, setDeepFilter] = useState("all")
  const [effectFilter, setEffectFilter] = useState<number[]>([])

  const char =
    allChars.find((c) => c.slot_index === selectedSlot) ?? allChars[0]
  const allRelics: Array<Record<string, unknown>> =
    (char?.relics as Array<Record<string, unknown>>) ?? []

  const relics = useMemo(() => {
    return applyFilters(
      allRelics,
      search,
      colorFilter,
      tierFilter,
      deepFilter,
      effectFilter,
      effectMap,
    )
  }, [
    allRelics,
    search,
    colorFilter,
    tierFilter,
    deepFilter,
    effectFilter,
    effectMap,
  ])

  if (allChars.length === 0) {
    return (
      <p className="text-muted-foreground py-8 text-center">
        No inventory loaded.{" "}
        <a href="/upload" className="underline">
          Upload a save file
        </a>{" "}
        first.
      </p>
    )
  }

  const handleCharChange = (slotStr: string) => {
    const slot = Number(slotStr)
    setSelectedSlot(slot)
    const picked = allChars.find((c) => c.slot_index === slot)
    if (picked)
      sessionStorage.setItem("selectedCharacter", JSON.stringify(picked))
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-3 items-center mb-4">
        {allChars.length > 1 && (
          <Select
            value={String(char?.slot_index ?? "")}
            onValueChange={handleCharChange}
          >
            <SelectTrigger className="w-48">
              <SelectValue placeholder="Select character" />
            </SelectTrigger>
            <SelectContent>
              {allChars.map((c) => (
                <SelectItem
                  key={c.slot_index as number}
                  value={String(c.slot_index)}
                >
                  {c.name as string} (Slot {c.slot_index as number})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <p className="text-sm text-muted-foreground">
          {allChars.length === 1 && (
            <>
              <strong>{char?.name as string}</strong> ·{" "}
            </>
          )}
          Session only —{" "}
          <a href="/login" className="underline">
            sign in
          </a>{" "}
          to save.
        </p>
      </div>

      <InventoryFilters
        search={search}
        setSearch={setSearch}
        colorFilter={colorFilter}
        setColorFilter={setColorFilter}
        tierFilter={tierFilter}
        setTierFilter={setTierFilter}
        deepFilter={deepFilter}
        setDeepFilter={setDeepFilter}
        effectFilter={effectFilter}
        setEffectFilter={setEffectFilter}
        effectsData={effectsData}
      />

      {relics.length === 0 ? (
        <p className="text-sm text-muted-foreground py-8 text-center">
          No relics match the current filters.
        </p>
      ) : (
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
                  {EffectList({
                    effectIds: [
                      relic.effect_1 as number,
                      relic.effect_2 as number,
                      relic.effect_3 as number,
                    ],
                    isCurse: false,
                    effectMap,
                  }) ?? (
                    <span className="text-xs text-muted-foreground italic">
                      —
                    </span>
                  )}
                </TableCell>
                <TableCell>
                  {EffectList({
                    effectIds: [
                      relic.curse_1 as number,
                      relic.curse_2 as number,
                      relic.curse_3 as number,
                    ],
                    isCurse: true,
                    effectMap,
                  }) ?? (
                    <span className="text-xs text-muted-foreground italic">
                      —
                    </span>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  )
}

function InventoryPage() {
  const { user } = useAuth()

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Relic Inventory</h1>
        <p className="text-muted-foreground mt-1">
          Browse relics from your save file.
        </p>
      </div>
      <Suspense fallback={<Skeleton className="h-48 w-full" />}>
        {user ? <AuthInventory /> : <AnonInventory />}
      </Suspense>
    </div>
  )
}
