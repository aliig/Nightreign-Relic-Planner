import { Filter, Search, X } from "lucide-react"
import { useMemo, useState } from "react"

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
import type { ManagedRelic } from "./types"

export type FilterState = {
  search: string
  colorFilter: string
  tierFilter: string
  deepFilter: string
  statusFilter: string
  effectFilter: number[]
  /** How to match the selected effects: all (AND) / any (OR) / none (NOT). */
  effectMode: "and" | "or" | "not"
}

export function applyFilters(
  relics: ManagedRelic[],
  f: FilterState,
  effectMap: Map<number, string>,
): ManagedRelic[] {
  return relics.filter((r) => {
    if (f.search && !r.name.toLowerCase().includes(f.search.toLowerCase()))
      return false
    if (f.colorFilter !== "all" && r.color !== f.colorFilter) return false
    if (f.tierFilter !== "all" && r.tier !== f.tierFilter) return false
    if (f.deepFilter === "deep" && !r.isDeep) return false
    if (f.deepFilter === "standard" && r.isDeep) return false

    if (f.effectFilter.length > 0) {
      const selectedNames = f.effectFilter
        .map((id) => effectMap.get(id))
        .filter(Boolean)
      const relicEffectNames = r.effects
        .map((id) => effectMap.get(id))
        .filter(Boolean)
      const has = (name: string | undefined) => relicEffectNames.includes(name)
      if (f.effectMode === "or") {
        if (!selectedNames.some(has)) return false
      } else if (f.effectMode === "not") {
        if (selectedNames.some(has)) return false
      } else if (!selectedNames.every(has)) {
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
  mode,
  onModeChange,
}: {
  effectsData: unknown[]
  selectedEffects: number[]
  onChange: (ids: number[]) => void
  mode: "and" | "or" | "not"
  onModeChange: (mode: "and" | "or" | "not") => void
}) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState("")

  const effects = useMemo(() => {
    const arr = (
      (effectsData as Array<{ id: number; name: string }>) ?? []
    ).filter((e) => typeof e.id === "number" && typeof e.name === "string")
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
          <DialogTitle>Filter by Effects</DialogTitle>
        </DialogHeader>
        <div className="px-4 pb-2">
          <div className="flex gap-1 rounded-md bg-muted/50 p-0.5">
            {(
              [
                ["and", "All"],
                ["or", "Any"],
                ["not", "None"],
              ] as const
            ).map(([m, label]) => (
              <Button
                key={m}
                type="button"
                size="sm"
                variant={mode === m ? "secondary" : "ghost"}
                className="h-7 flex-1 text-xs"
                onClick={() => onModeChange(m)}
              >
                {label}
              </Button>
            ))}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Match relics with{" "}
            <strong>
              {mode === "or" ? "any" : mode === "not" ? "none" : "all"}
            </strong>{" "}
            of the selected effects.
          </p>
        </div>
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

export function InventoryFilters({
  filter,
  setFilter,
  effectsData,
}: {
  filter: FilterState
  setFilter: (next: FilterState) => void
  effectsData: unknown[]
}) {
  const patch = (p: Partial<FilterState>) => setFilter({ ...filter, ...p })
  return (
    <div className="flex flex-wrap gap-3">
      <Input
        placeholder="Search by name…"
        value={filter.search}
        onChange={(e) => patch({ search: e.target.value })}
        className="w-48"
      />
      <Select
        value={filter.colorFilter}
        onValueChange={(v) => patch({ colorFilter: v })}
      >
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
      <Select
        value={filter.tierFilter}
        onValueChange={(v) => patch({ tierFilter: v })}
      >
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
      <Select
        value={filter.deepFilter}
        onValueChange={(v) => patch({ deepFilter: v })}
      >
        <SelectTrigger className="w-32">
          <SelectValue placeholder="Type" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All Types</SelectItem>
          <SelectItem value="standard">Standard</SelectItem>
          <SelectItem value="deep">Deep</SelectItem>
        </SelectContent>
      </Select>
      <Select
        value={filter.statusFilter}
        onValueChange={(v) => patch({ statusFilter: v })}
      >
        <SelectTrigger className="w-36">
          <SelectValue placeholder="Status" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All status</SelectItem>
          <SelectItem value="unused">Unused</SelectItem>
          <SelectItem value="stale">Stale (equipped)</SelectItem>
          <SelectItem value="bench">On the bench</SelectItem>
          <SelectItem value="active">Active</SelectItem>
        </SelectContent>
      </Select>
      <EffectMultiSelect
        effectsData={effectsData}
        selectedEffects={filter.effectFilter}
        onChange={(ids) => patch({ effectFilter: ids })}
        mode={filter.effectMode}
        onModeChange={(m) => patch({ effectMode: m })}
      />
    </div>
  )
}

export const EMPTY_FILTER: FilterState = {
  search: "",
  colorFilter: "all",
  tierFilter: "all",
  deepFilter: "all",
  statusFilter: "all",
  effectFilter: [],
  effectMode: "and",
}
