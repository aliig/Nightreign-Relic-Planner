import { ChevronDown, Search, X } from "lucide-react"
import { useMemo, useState } from "react"

import { COLOR_HEX, RELIC_COLORS, RELIC_TIERS } from "@/components/RelicDisplay"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { cn } from "@/lib/utils"
import {
  activeFilterChips,
  EMPTY_FILTER,
  type FilterState,
  stateFacetCount,
  type TriState,
} from "./relicFilter"

type FacetProps = {
  f: FilterState
  set: (patch: Partial<FilterState>) => void
}

const TRI_OPTIONS: { value: string; label: string }[] = [
  { value: "all", label: "Any" },
  { value: "yes", label: "Yes" },
  { value: "no", label: "No" },
]

/** Shared trigger content: label + active-count badge + chevron. */
function FacetInner({ label, count }: { label: string; count?: number }) {
  return (
    <>
      {label}
      {count ? (
        <Badge
          variant="secondary"
          className="h-5 min-w-5 justify-center rounded-full px-1 text-xs tabular-nums"
        >
          {count}
        </Badge>
      ) : null}
      <ChevronDown className="h-3.5 w-3.5 opacity-50" />
    </>
  )
}

/** A compact single-row segmented control (used for the tri-state State axes). */
function Segmented({
  value,
  onChange,
  options,
}: {
  value: string
  onChange: (v: string) => void
  options: { value: string; label: string }[]
}) {
  return (
    <div className="inline-flex w-full gap-0.5 rounded-md bg-muted/50 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={cn(
            "flex-1 rounded-sm px-2 py-1 text-xs transition-colors",
            value === o.value
              ? "bg-background font-medium shadow-sm"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

function ColorFacet({ f, set }: FacetProps) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" className="h-9 gap-1.5">
          <FacetInner label="Color" count={f.colors.length} />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-44 p-1.5">
        {RELIC_COLORS.map((c) => {
          const checked = f.colors.includes(c)
          return (
            <button
              key={c}
              type="button"
              onClick={() =>
                set({
                  colors: checked
                    ? f.colors.filter((x) => x !== c)
                    : [...f.colors, c],
                })
              }
              className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent"
            >
              <Checkbox
                checked={checked}
                tabIndex={-1}
                className="pointer-events-none"
              />
              <span
                className="h-3 w-3 rounded-full border border-border"
                style={{ backgroundColor: COLOR_HEX[c] }}
              />
              {c}
            </button>
          )
        })}
      </PopoverContent>
    </Popover>
  )
}

function TierFacet({ f, set }: FacetProps) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" className="h-9 gap-1.5">
          <FacetInner label="Tier" count={f.tiers.length} />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-44 p-1.5">
        {RELIC_TIERS.map((t) => {
          const checked = f.tiers.includes(t)
          return (
            <button
              key={t}
              type="button"
              onClick={() =>
                set({
                  tiers: checked
                    ? f.tiers.filter((x) => x !== t)
                    : [...f.tiers, t],
                })
              }
              className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent"
            >
              <Checkbox
                checked={checked}
                tabIndex={-1}
                className="pointer-events-none"
              />
              {t}
            </button>
          )
        })}
      </PopoverContent>
    </Popover>
  )
}

function TypeFacet({ f, set }: FacetProps) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" className="h-9 gap-1.5">
          <FacetInner label="Type" count={f.deep !== "all" ? 1 : 0} />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-56 p-2">
        <Segmented
          value={f.deep}
          onChange={(v) => set({ deep: v as FilterState["deep"] })}
          options={[
            { value: "all", label: "Any" },
            { value: "standard", label: "Standard" },
            { value: "deep", label: "Deep" },
          ]}
        />
      </PopoverContent>
    </Popover>
  )
}

function StateFacet({ f, set }: FacetProps) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" className="h-9 gap-1.5">
          <FacetInner label="State" count={stateFacetCount(f)} />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64 space-y-3 p-3">
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground">
            Sellability
          </p>
          <Segmented
            value={f.sellable}
            onChange={(v) => set({ sellable: v as FilterState["sellable"] })}
            options={[
              { value: "all", label: "Any" },
              { value: "sellable", label: "Sellable" },
              { value: "locked", label: "Locked" },
            ]}
          />
        </div>
        <div className="h-px bg-border" />
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground">Equipped</p>
          <Segmented
            value={f.equipped}
            onChange={(v) => set({ equipped: v as TriState })}
            options={TRI_OPTIONS}
          />
        </div>
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground">
            In a build
          </p>
          <Segmented
            value={f.used}
            onChange={(v) => set({ used: v as TriState })}
            options={TRI_OPTIONS}
          />
        </div>
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground">
            Bookmarked
          </p>
          <Segmented
            value={f.bookmarked}
            onChange={(v) => set({ bookmarked: v as TriState })}
            options={TRI_OPTIONS}
          />
        </div>
      </PopoverContent>
    </Popover>
  )
}

function EffectsFacet({
  f,
  set,
  effectsData,
}: FacetProps & { effectsData: unknown[] }) {
  const [search, setSearch] = useState("")
  const selected = f.effectFilter
  const setEffects = (ids: number[]) => set({ effectFilter: ids })

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
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" className="h-9 gap-1.5">
          <FacetInner label="Effects" count={selected.length} />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 p-0">
        <div className="p-2 pb-1">
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
                variant={f.effectMode === m ? "secondary" : "ghost"}
                className="h-7 flex-1 text-xs"
                onClick={() => set({ effectMode: m })}
              >
                {label}
              </Button>
            ))}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Match relics with{" "}
            <strong>
              {f.effectMode === "or"
                ? "any"
                : f.effectMode === "not"
                  ? "none"
                  : "all"}
            </strong>{" "}
            of the selected effects.
          </p>
        </div>
        <div className="relative px-2 pb-2">
          <Search className="absolute left-4 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search effects..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8"
          />
        </div>
        {selected.length > 0 && (
          <div className="flex max-h-24 flex-wrap gap-1 overflow-y-auto px-2 pb-2">
            {selected.map((id) => {
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
                    onClick={() => setEffects(selected.filter((x) => x !== id))}
                    className="ml-1 hover:text-destructive"
                    aria-label={`Remove ${e.name}`}
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
            <div className="flex flex-col gap-1 p-2">
              {effects.map((e) => {
                const isSelected = selected.includes(e.id)
                return (
                  <button
                    key={e.id}
                    type="button"
                    onClick={() =>
                      isSelected
                        ? setEffects(selected.filter((id) => id !== e.id))
                        : setEffects([...selected, e.id])
                    }
                    className={cn(
                      "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm hover:bg-accent",
                      isSelected && "bg-accent/50",
                    )}
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
      </PopoverContent>
    </Popover>
  )
}

/** The filter facet controls (search + Color/Tier/Type/State/Effects popovers).
 *  Returns a fragment so the caller can lay them out alongside Sort etc. */
export function InventoryFilters({
  filter,
  setFilter,
  effectsData,
}: {
  filter: FilterState
  setFilter: (next: FilterState) => void
  effectsData: unknown[]
}) {
  const set = (patch: Partial<FilterState>) =>
    setFilter({ ...filter, ...patch })
  return (
    <>
      <div className="relative">
        <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          placeholder="Search by name…"
          value={filter.search}
          onChange={(e) => set({ search: e.target.value })}
          className="h-9 w-48 pl-8"
        />
      </div>
      <ColorFacet f={filter} set={set} />
      <TierFacet f={filter} set={set} />
      <TypeFacet f={filter} set={set} />
      <StateFacet f={filter} set={set} />
      <EffectsFacet f={filter} set={set} effectsData={effectsData} />
    </>
  )
}

/** Removable chips summarizing the active filter, plus Clear-all. */
export function ActiveFilterChips({
  filter,
  setFilter,
  effectMap,
}: {
  filter: FilterState
  setFilter: (next: FilterState) => void
  effectMap: Map<number, string>
}) {
  const chips = activeFilterChips(filter, effectMap)
  if (chips.length === 0) return null
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-xs text-muted-foreground">Filters:</span>
      {chips.map((chip) => (
        <Badge key={chip.key} variant="secondary" className="gap-1 font-normal">
          {chip.label}
          <button
            type="button"
            onClick={() => setFilter({ ...filter, ...chip.clear })}
            className="hover:text-destructive"
            aria-label={`Remove filter ${chip.label}`}
          >
            <X className="h-3 w-3" />
          </button>
        </Badge>
      ))}
      <Button
        variant="ghost"
        size="sm"
        className="h-6 px-2 text-xs"
        onClick={() => setFilter(EMPTY_FILTER)}
      >
        Clear all
      </Button>
    </div>
  )
}
