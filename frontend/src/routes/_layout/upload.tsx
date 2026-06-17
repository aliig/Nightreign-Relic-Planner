import { useMutation, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import { AlertCircle, Info, Upload, User2 } from "lucide-react"
import { useRef, useState } from "react"

import type { BuildChange } from "@/client"
import { SavesService } from "@/client"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { isLoggedIn } from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"
import { storeAnonUploadMeta, useSaveStatus } from "@/hooks/useSaveStatus"
import {
  type ChangeDescription,
  describeBuildChange,
  rawScoreTooltip,
  relicSummary,
} from "@/lib/buildChange"
import { computeOverallPct, optimizingLabel } from "@/lib/optimizeProgress"
import { formatRelativeTime, handleError } from "@/utils"

export const Route = createFileRoute("/_layout/upload")({
  component: UploadPage,
  head: () => ({
    meta: [{ title: "Upload Save - Nightreign Relic Planner" }],
  }),
})

function SaveStatusBanner() {
  const { status, isLoading, isAnon } = useSaveStatus()

  if (isLoading || !status) return null

  return (
    <Alert>
      <Info className="h-4 w-4" />
      <AlertTitle>
        {isAnon ? "Session data loaded" : "Save data on file"}
      </AlertTitle>
      <AlertDescription>
        <div className="flex flex-wrap items-center gap-2 mt-1">
          <Badge variant="secondary">{status.platform}</Badge>
          <span>
            {status.profile_count} profile
            {status.profile_count !== 1 ? "s" : ""}
            {status.profile_names.length > 0 &&
              `: ${status.profile_names.join(", ")}`}
          </span>
        </div>
        {!isAnon && status.uploaded_at && (
          <p className="mt-1 text-xs">
            Uploaded {formatRelativeTime(status.uploaded_at)} — drop a new file
            to replace.
          </p>
        )}
        {isAnon && (
          <p className="mt-1 text-xs">
            Session only — drop a new file to refresh, or{" "}
            <a href="/login" className="underline">
              sign in
            </a>{" "}
            to persist your data.
          </p>
        )}
      </AlertDescription>
    </Alert>
  )
}

interface StreamUploadProgress {
  phase: "parsing" | "optimizing" | "done"
  buildIndex?: number
  buildTotal?: number
  buildName?: string
  vessel?: number
  vesselTotal?: number
  vesselName?: string
}

interface StreamUploadResult {
  profiles: Array<{
    slot_index: number
    name: string
    relic_count: number
    id?: string
  }>
  profileCount: number
  platform: string
  relicDelta?: { added: number; removed: number }
  changes: BuildChange[]
}

async function runUploadStream(
  file: File,
  onProgress: (p: StreamUploadProgress) => void,
): Promise<StreamUploadResult> {
  const token = localStorage.getItem("access_token")
  const formData = new FormData()
  formData.append("file", file)

  const headers: HeadersInit = {}
  if (token)
    (headers as Record<string, string>).Authorization = `Bearer ${token}`

  const response = await fetch("/api/v1/saves/upload/stream", {
    method: "POST",
    headers,
    body: formData,
  })

  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: "Upload failed" }))
    throw new Error(err.detail ?? "Upload failed")
  }

  const reader = response.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  let uploadData: any = null
  const changes: BuildChange[] = []

  // Accumulate progress so per-build context (which build, of how many) survives
  // into the per-vessel ticks — the backend sends build identity only on
  // `optimize_start`, not on each `optimize_progress`.
  let progress: StreamUploadProgress = { phase: "parsing" }
  const emit = (patch: Partial<StreamUploadProgress>) => {
    progress = { ...progress, ...patch }
    onProgress(progress)
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    const parts = buffer.split("\n\n")
    buffer = parts.pop() ?? ""

    for (const part of parts) {
      const dataLine = part.split("\n").find((l) => l.startsWith("data: "))
      if (!dataLine) continue
      const payload = JSON.parse(dataLine.slice(6))

      if (payload.type === "upload_complete") {
        uploadData = payload.data
        emit({ phase: "optimizing" })
      } else if (payload.type === "optimize_start") {
        // New build: set its identity, clear the prior build's vessel sub-progress.
        emit({
          buildIndex: payload.index,
          buildTotal: payload.total,
          buildName: payload.build_name,
          vessel: undefined,
          vesselTotal: undefined,
          vesselName: undefined,
        })
      } else if (payload.type === "optimize_progress") {
        // Vessel tick: merges onto the current build's identity from optimize_start.
        emit({
          vessel: payload.vessel,
          vesselTotal: payload.total,
          vesselName: payload.name,
        })
      } else if (payload.type === "optimize_done") {
        if (payload.change) changes.push(payload.change as BuildChange)
      } else if (payload.type === "complete") {
        emit({ phase: "done" })
        return {
          profiles: uploadData?.profiles ?? [],
          profileCount: uploadData?.profile_count ?? 0,
          platform: uploadData?.platform ?? "PC",
          relicDelta: uploadData?.relic_delta,
          changes,
        }
      }
    }
  }

  if (uploadData) {
    return {
      profiles: uploadData.profiles ?? [],
      profileCount: uploadData.profile_count ?? 0,
      platform: uploadData.platform ?? "PC",
      relicDelta: uploadData.relic_delta,
      changes,
    }
  }

  throw new Error("Stream ended without completion")
}

function ChangesSummary({ changes }: { changes: BuildChange[] }) {
  const rows = changes
    .map((change) => ({ change, d: describeBuildChange(change) }))
    .filter(
      (r): r is { change: BuildChange; d: ChangeDescription } => r.d !== null,
    )
  if (rows.length === 0) return null

  return (
    <Alert>
      <Info className="h-4 w-4" />
      <AlertTitle>
        {rows.length} build{rows.length !== 1 ? "s" : ""} updated
      </AlertTitle>
      <AlertDescription>
        <ul className="mt-1 space-y-1">
          {rows.map(({ change: c, d }) => {
            const Icon = d.icon
            return (
              <li
                key={`${c.build_id}-${c.slot_index}`}
                className="flex flex-col"
              >
                <div className="flex items-center gap-1.5">
                  <Link
                    to="/builds/$buildId/optimize"
                    params={{ buildId: c.build_id ?? "" }}
                    className="underline"
                  >
                    {c.build_name || "Build"}
                  </Link>
                  <span
                    className={`inline-flex items-center gap-0.5 text-xs font-medium ${d.textClass}`}
                    title={rawScoreTooltip(d.rawScore)}
                  >
                    <Icon className="h-3 w-3" />
                    {d.headline}
                  </span>
                  {d.reliable === false && (
                    <span className="text-xs opacity-70">(approximate)</span>
                  )}
                </div>
                {d.relics && (
                  <span className="pl-0.5 text-xs text-muted-foreground">
                    {d.relics.verb} {relicSummary(d.relics)}
                  </span>
                )}
              </li>
            )
          })}
        </ul>
      </AlertDescription>
    </Alert>
  )
}

function UploadPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  // Streaming upload state (authenticated)
  const [streamProgress, setStreamProgress] =
    useState<StreamUploadProgress | null>(null)
  const [streamResult, setStreamResult] = useState<StreamUploadResult | null>(
    null,
  )
  const [streamError, setStreamError] = useState<string | null>(null)
  const [isStreaming, setIsStreaming] = useState(false)

  // Legacy mutation state (anonymous)
  const [uploadResult, setUploadResult] = useState<Awaited<
    ReturnType<typeof SavesService.uploadSave>
  > | null>(null)

  const uploadMutation = useMutation({
    mutationFn: (file: File) => SavesService.uploadSave({ formData: { file } }),
    onSuccess: (data) => {
      setUploadResult(data)
      queryClient.invalidateQueries({ queryKey: ["profiles"] })
      queryClient.invalidateQueries({ queryKey: ["save-status"] })
      if (!data.persisted) {
        storeAnonUploadMeta({
          profile_count: data.profile_count,
          profile_names: data.profiles.map((p) => p.name),
          platform: data.platform,
          uploaded_at: new Date().toISOString(),
        })
        sessionStorage.setItem("parsedProfiles", JSON.stringify(data.profiles))
        if (data.profiles.length > 0) {
          sessionStorage.setItem(
            "selectedProfile",
            JSON.stringify(data.profiles[0]),
          )
        }
      }
      showSuccessToast(
        `Save imported — ${data.profile_count} profile${data.profile_count !== 1 ? "s" : ""} found.`,
      )
    },
    onError: handleError.bind(showErrorToast),
  })

  async function handleStreamUpload(file: File) {
    setIsStreaming(true)
    setStreamProgress({ phase: "parsing" })
    setStreamResult(null)
    setStreamError(null)
    try {
      const result = await runUploadStream(file, setStreamProgress)
      setStreamResult(result)
      queryClient.invalidateQueries({ queryKey: ["profiles"] })
      queryClient.invalidateQueries({ queryKey: ["save-status"] })
      queryClient.invalidateQueries({ queryKey: ["builds"] })
      queryClient.invalidateQueries({ queryKey: ["snapshot"] })

      const meaningful = result.changes.filter(
        (c) => c.status !== "unchanged" && c.status !== "new",
      )
      if (meaningful.length > 0) {
        showSuccessToast(
          `Save imported — ${meaningful.length} build${meaningful.length !== 1 ? "s" : ""} re-optimized.`,
        )
      } else {
        showSuccessToast(
          `Save imported — ${result.profileCount} profile${result.profileCount !== 1 ? "s" : ""} found.`,
        )
      }
    } catch (err) {
      setStreamError(err instanceof Error ? err.message : "Upload failed")
      showErrorToast(err instanceof Error ? err.message : "Upload failed")
    } finally {
      setIsStreaming(false)
      setStreamProgress(null)
    }
  }

  function handleFile(file: File) {
    const name = file.name.toLowerCase()
    if (!name.endsWith(".sl2") && !name.endsWith(".dat")) {
      showErrorToast("Please upload a .sl2 (PC) or memory.dat (PS4) file.")
      return
    }
    if (isLoggedIn()) {
      handleStreamUpload(file)
    } else {
      uploadMutation.mutate(file)
    }
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files[0]
    if (file) handleFile(file)
  }

  function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (file) handleFile(file)
  }

  const isPending = isStreaming || uploadMutation.isPending
  const showError = streamError || uploadMutation.isError

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Upload Save File</h1>
        <p className="text-muted-foreground mt-1">
          Import your PC (.sl2) or PS4 (memory.dat) save to load your relic
          inventory.
        </p>
      </div>

      <SaveStatusBanner />

      {/* Drop zone */}
      <div
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => fileInputRef.current?.click()}
        className={`
          flex flex-col items-center justify-center rounded-lg border-2 border-dashed
          p-12 cursor-pointer transition-colors
          ${dragging ? "border-primary bg-primary/5" : "border-muted-foreground/25 hover:border-primary/50 hover:bg-muted/30"}
        `}
      >
        <Upload className="h-10 w-10 text-muted-foreground mb-3" />
        <p className="text-sm font-medium">Drop your save file here</p>
        <p className="text-xs text-muted-foreground mt-1">or click to browse</p>
        <p className="text-xs text-muted-foreground mt-3">
          .sl2 (PC) · memory.dat (PS4)
        </p>
        <p className="text-xs text-muted-foreground mt-2">
          PC:{" "}
          <code className="font-mono">
            %AppData%\Roaming\Nightreign\[SteamID]\NR0000.sl2
          </code>
        </p>
        <input
          ref={fileInputRef}
          type="file"
          accept=".sl2,.dat"
          className="hidden"
          onChange={onFileChange}
        />
      </div>

      {/* Progress */}
      {isPending && (
        <div className="space-y-2">
          {streamProgress?.phase === "parsing" && (
            <p className="text-sm text-muted-foreground animate-pulse">
              Parsing save file…
            </p>
          )}
          {streamProgress?.phase === "optimizing" && (
            <>
              <p className="text-sm text-muted-foreground">
                {optimizingLabel(streamProgress)}
              </p>
              <Progress value={computeOverallPct(streamProgress)} />
            </>
          )}
          {!streamProgress && (
            <p className="text-sm text-muted-foreground animate-pulse">
              Parsing save file…
            </p>
          )}
        </div>
      )}

      {/* Error */}
      {showError && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>
            {streamError ?? (uploadMutation.error as Error)?.message}
          </AlertDescription>
        </Alert>
      )}

      {/* Stream results (authenticated) */}
      {streamResult && (
        <div className="space-y-3">
          <h2 className="text-lg font-medium">
            Found {streamResult.profileCount} profile
            {streamResult.profileCount !== 1 ? "s" : ""}
          </h2>
          <ChangesSummary changes={streamResult.changes} />
          <div className="grid gap-3 sm:grid-cols-2">
            {streamResult.profiles.map((prof) => (
              <Card
                key={prof.slot_index}
                className="cursor-pointer hover:border-primary/50 transition-colors"
                onClick={() => {
                  sessionStorage.setItem(
                    "selectedProfile",
                    JSON.stringify(prof),
                  )
                  navigate({ to: "/inventory" })
                }}
              >
                <CardHeader className="pb-2">
                  <div className="flex items-center gap-2">
                    <User2 className="h-4 w-4 text-muted-foreground" />
                    <CardTitle className="text-base">{prof.name}</CardTitle>
                  </div>
                </CardHeader>
                <CardContent>
                  <CardDescription>
                    Slot {prof.slot_index} · {prof.relic_count} relic
                    {prof.relic_count !== 1 ? "s" : ""}
                  </CardDescription>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      )}

      {/* Legacy results (anonymous) */}
      {uploadResult && !streamResult && (
        <div className="space-y-3">
          <h2 className="text-lg font-medium">
            Found {uploadResult.profile_count} profile
            {uploadResult.profile_count !== 1 ? "s" : ""}
          </h2>
          <div className="grid gap-3 sm:grid-cols-2">
            {uploadResult.profiles.map((prof) => (
              <Card
                key={prof.slot_index}
                className="cursor-pointer hover:border-primary/50 transition-colors"
                onClick={() => {
                  sessionStorage.setItem(
                    "selectedProfile",
                    JSON.stringify(prof),
                  )
                  navigate({ to: "/inventory" })
                }}
              >
                <CardHeader className="pb-2">
                  <div className="flex items-center gap-2">
                    <User2 className="h-4 w-4 text-muted-foreground" />
                    <CardTitle className="text-base">{prof.name}</CardTitle>
                  </div>
                </CardHeader>
                <CardContent>
                  <CardDescription>
                    Slot {prof.slot_index} · {prof.relic_count} relic
                    {prof.relic_count !== 1 ? "s" : ""}
                  </CardDescription>
                </CardContent>
              </Card>
            ))}
          </div>
          {!uploadResult.persisted && (
            <Alert>
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>
                You're not logged in — your inventory won't be saved between
                sessions.{" "}
                <a href="/login" className="underline">
                  Sign in
                </a>{" "}
                to persist your data.
              </AlertDescription>
            </Alert>
          )}
        </div>
      )}
    </div>
  )
}
