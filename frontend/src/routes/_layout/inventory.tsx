import { useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { Package } from "lucide-react"
import { Suspense, useMemo, useState } from "react"

import { GameService, SavesService } from "@/client"
import { EmptyState } from "@/components/Common/EmptyState"
import { SaveFreshness } from "@/components/Common/SaveFreshness"
import { RelicManager } from "@/components/inventory/RelicManager"
import type { ManagedRelic } from "@/components/inventory/types"
import { buildEffectMap } from "@/components/RelicDisplay"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import useAuth from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"
import { useRelicUsage } from "@/hooks/useRelicUsage"
import { useReconcileSlotBases } from "@/lib/pendingChanges"

export const Route = createFileRoute("/_layout/inventory")({
  component: InventoryPage,
  head: () => ({
    meta: [{ title: "Inventory - Nightreign Relic Planner" }],
  }),
})

// --- Authenticated inventory ---

function AuthInventoryBody({
  profileId,
  slotIndex,
  murks,
  effectsData,
  effectMap,
}: {
  profileId: string
  slotIndex: number
  murks: number
  effectsData: unknown[]
  effectMap: Map<number, string>
}) {
  const { data } = useSuspenseQuery({
    queryKey: ["relics", profileId],
    queryFn: () => SavesService.getProfileRelics({ profileId }),
    staleTime: 5 * 60 * 1000,
  })
  const { usage } = useRelicUsage(profileId)

  const relics: ManagedRelic[] = useMemo(
    () =>
      (data.data ?? []).map((r) => ({
        key: r.id,
        gaHandle: r.ga_handle,
        realId: r.real_id,
        name: r.name,
        color: r.color,
        tier: r.tier,
        isDeep: r.is_deep,
        effects: [r.effect_1, r.effect_2, r.effect_3],
        curses: [r.curse_1, r.curse_2, r.curse_3],
        isFavorite: r.is_favorite ?? false,
        equipped: r.equipped ?? false,
      })),
    [data.data],
  )

  return (
    <RelicManager
      relics={relics}
      effectsData={effectsData}
      effectMap={effectMap}
      usage={usage}
      slotIndex={slotIndex}
      murks={murks}
    />
  )
}

function AuthInventory() {
  const { data: profiles } = useSuspenseQuery({
    queryKey: ["profiles"],
    queryFn: () => SavesService.listProfiles(),
    staleTime: 5 * 60 * 1000,
  })
  const { data: effectsData } = useSuspenseQuery({
    queryKey: ["game", "effects"],
    queryFn: () => GameService.getEffects(),
    staleTime: Number.POSITIVE_INFINITY,
  })

  const effectMap = useMemo(
    () => buildEffectMap((effectsData ?? []) as unknown[]),
    [effectsData],
  )

  const [selectedId, setSelectedId] = useState<string | null>(
    profiles.data?.[0]?.id ?? null,
  )

  const { showErrorToast } = useCustomToast()
  // Drop any pending edits whose underlying save was re-uploaded (here or on
  // another device) since they were made — they'd otherwise mis-apply.
  useReconcileSlotBases(
    (profiles.data ?? []).map((p) => ({ slot: p.slot_index, id: p.id })),
    (slots) =>
      showErrorToast(
        `Cleared unsynced edits for slot ${slots.join(", ")} — this save was re-uploaded since you made them.`,
      ),
  )

  if (!profiles.data?.length) {
    return (
      <EmptyState
        icon={Package}
        title="No save loaded"
        action={
          <Button asChild size="sm">
            <Link to="/upload">Upload a save file</Link>
          </Button>
        }
      >
        Import your .sl2 or memory.dat to load your relic inventory.
      </EmptyState>
    )
  }

  const selected =
    profiles.data.find((p) => p.id === selectedId) ?? profiles.data[0]

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-3 items-center">
        {profiles.data.length > 1 ? (
          <Select value={selected.id} onValueChange={setSelectedId}>
            <SelectTrigger className="w-48">
              <SelectValue placeholder="Select profile" />
            </SelectTrigger>
            <SelectContent>
              {profiles.data.map((c) => (
                <SelectItem key={c.id} value={c.id}>
                  {c.name} (Slot {c.slot_index})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <p className="text-sm text-muted-foreground">
            <strong>{selected.name}</strong>
          </p>
        )}
      </div>

      <Suspense fallback={<Skeleton className="h-48 w-full" />}>
        <AuthInventoryBody
          key={selected.id}
          profileId={selected.id}
          slotIndex={selected.slot_index}
          murks={selected.murks ?? 0}
          effectsData={(effectsData ?? []) as unknown[]}
          effectMap={effectMap}
        />
      </Suspense>
    </div>
  )
}

// --- Anonymous inventory ---

function AnonInventory() {
  const { data: effectsData } = useSuspenseQuery({
    queryKey: ["game", "effects"],
    queryFn: () => GameService.getEffects(),
    staleTime: Number.POSITIVE_INFINITY,
  })

  const effectMap = useMemo(
    () => buildEffectMap((effectsData ?? []) as unknown[]),
    [effectsData],
  )
  const { usage } = useRelicUsage(null)

  const allProfiles: Array<Record<string, any>> = JSON.parse(
    sessionStorage.getItem("parsedProfiles") ?? "[]",
  )

  const defaultProfile = (() => {
    try {
      return JSON.parse(sessionStorage.getItem("selectedProfile") ?? "null")
    } catch {
      return null
    }
  })()

  const defaultSlot =
    defaultProfile?.slot_index ?? allProfiles[0]?.slot_index ?? null
  const [selectedSlot, setSelectedSlot] = useState<number | null>(defaultSlot)

  const profile =
    allProfiles.find((c) => c.slot_index === selectedSlot) ?? allProfiles[0]

  const relics: ManagedRelic[] = useMemo(() => {
    const list: Array<Record<string, any>> = profile?.relics ?? []
    return list.map((r, i) => ({
      key: `${r.ga_handle ?? i}`,
      gaHandle: Number(r.ga_handle),
      realId: Number(r.real_id),
      name: r.name,
      color: r.color,
      tier: r.tier,
      isDeep: !!r.is_deep,
      effects: [r.effect_1, r.effect_2, r.effect_3],
      curses: [r.curse_1, r.curse_2, r.curse_3],
      isFavorite: !!r.is_favorite,
      equipped: !!r.equipped,
    }))
  }, [profile])

  if (allProfiles.length === 0) {
    return (
      <EmptyState
        icon={Package}
        title="No save loaded"
        action={
          <Button asChild size="sm">
            <Link to="/upload">Upload a save file</Link>
          </Button>
        }
      >
        Import your .sl2 or memory.dat to load your relic inventory.
      </EmptyState>
    )
  }

  const handleProfileChange = (slotStr: string) => {
    const slot = Number(slotStr)
    setSelectedSlot(slot)
    const picked = allProfiles.find((c) => c.slot_index === slot)
    if (picked)
      sessionStorage.setItem("selectedProfile", JSON.stringify(picked))
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-3 items-center mb-4">
        {allProfiles.length > 1 && (
          <Select
            value={String(profile?.slot_index ?? "")}
            onValueChange={handleProfileChange}
          >
            <SelectTrigger className="w-48">
              <SelectValue placeholder="Select profile" />
            </SelectTrigger>
            <SelectContent>
              {allProfiles.map((p) => (
                <SelectItem
                  key={p.slot_index as number}
                  value={String(p.slot_index)}
                >
                  {p.name as string} (Slot {p.slot_index as number})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <p className="text-sm text-muted-foreground">
          {allProfiles.length === 1 && (
            <>
              <strong>{profile?.name as string}</strong> ·{" "}
            </>
          )}
          Session only —{" "}
          <a href="/login" className="underline">
            sign in
          </a>{" "}
          to save.
        </p>
      </div>

      <RelicManager
        key={profile?.slot_index}
        relics={relics}
        effectsData={(effectsData ?? []) as unknown[]}
        effectMap={effectMap}
        usage={usage}
        slotIndex={Number(profile?.slot_index ?? 0)}
        murks={Number(profile?.murks ?? 0)}
      />
    </div>
  )
}

function InventoryPage() {
  const { user } = useAuth()

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold">Relic Inventory</h1>
          <p className="text-muted-foreground mt-1">
            Browse relics, bookmark keepers, and trash unused ones for Murk.
            Changes apply here instantly — export from the Changes panel when
            you're done.
          </p>
        </div>
        <SaveFreshness className="mt-1 shrink-0" />
      </div>
      <Suspense fallback={<Skeleton className="h-48 w-full" />}>
        {user ? <AuthInventory /> : <AnonInventory />}
      </Suspense>
    </div>
  )
}
