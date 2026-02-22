import { useQuery } from "@tanstack/react-query"
import { SavesService, type SaveStatusPublic } from "@/client"
import { isLoggedIn } from "@/hooks/useAuth"

const ANON_UPLOAD_KEY = "anon_upload_meta"

export interface AnonSaveStatus {
  character_count: number
  character_names: string[]
  platform: string
  uploaded_at: string
}

export function storeAnonUploadMeta(meta: AnonSaveStatus): void {
  sessionStorage.setItem(ANON_UPLOAD_KEY, JSON.stringify(meta))
}

export function getAnonUploadMeta(): AnonSaveStatus | null {
  try {
    const raw = sessionStorage.getItem(ANON_UPLOAD_KEY)
    return raw ? (JSON.parse(raw) as AnonSaveStatus) : null
  } catch {
    return null
  }
}

export type SaveStatusResult =
  | { status: SaveStatusPublic | null; isLoading: boolean; isAnon: false }
  | { status: (AnonSaveStatus & { id: "anon" }) | null; isLoading: false; isAnon: true }

export function useSaveStatus(): SaveStatusResult {
  const loggedIn = isLoggedIn()

  const { data: serverStatus, isLoading } = useQuery<SaveStatusPublic | null>({
    queryKey: ["save-status"],
    queryFn: () => SavesService.getSaveStatus(),
    enabled: loggedIn,
    staleTime: 5 * 60 * 1000,
  })

  if (loggedIn) {
    return {
      status: serverStatus ?? null,
      isLoading,
      isAnon: false,
    }
  }

  const anonMeta = getAnonUploadMeta()
  return {
    status: anonMeta ? { ...anonMeta, id: "anon" as const } : null,
    isLoading: false,
    isAnon: true,
  }
}
