import { useMutation, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import { AlertCircle, Info, Upload, User2 } from "lucide-react"
import { useEffect, useRef, useState } from "react"

import type { BuildChange } from "@/client"
import { SavesService } from "@/client"
import { ChangeRelicGroups } from "@/components/ChangeRelics"
import { OriginalBackupCard } from "@/components/OriginalBackupCard"
import { UploadGateDialog } from "@/components/UploadGateDialog"
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
import { useEffectMap } from "@/hooks/useEffectMap"
import { storeAnonUploadMeta, useSaveStatus } from "@/hooks/useSaveStatus"
import {
  type ChangeDescription,
  describeBuildChange,
  rawScoreTooltip,
} from "@/lib/buildChange"
import {
  type StreamUploadResult,
  startUpload,
  useOptimizeJob,
} from "@/lib/optimizeJobs"
import { computeOverallPct, optimizingLabel } from "@/lib/optimizeProgress"
import {
  clearAll,
  readAll,
  type SlotSummary,
  summarizePending,
} from "@/lib/pendingChanges"
import { storeOriginalBackup } from "@/lib/saveBackup"
import { rememberSaveFile } from "@/lib/saveFile"
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

function ChangesSummary({ changes }: { changes: BuildChange[] }) {
  const effectMap = useEffectMap()
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
                <div className="pl-0.5">
                  <ChangeRelicGroups groups={d.groups} effectMap={effectMap} />
                  {d.note && (
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {d.note}
                    </p>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      </AlertDescription>
    </Alert>
  )
}

/**
 * Why the save-to-save comparison did (or didn't) run.
 *
 * Suppression used to be silent, which is indistinguishable from "nothing
 * changed" — a player testing a friend's save saw an empty summary and no way
 * to tell whether the app had compared their own collection against it.
 */
function ComparisonNote({
  comparison,
}: {
  comparison?: StreamUploadResult["comparison"]
}) {
  if (!comparison) return null
  const { compared, reason, restarted_slots: restarted } = comparison
  const lines: string[] = []

  if (!compared && reason === "different_account") {
    lines.push(
      "This save belongs to a different Steam account than your last upload, so it wasn't compared against it — every relic here reads as new.",
    )
  } else if (!compared && reason === "no_previous_save") {
    // Nothing to say: a first upload has nothing to be compared against.
    return null
  } else if (reason === "unverified_owner") {
    lines.push(
      "We couldn't read this save's account ID (console saves don't carry one), so it was compared against your last upload without verifying they're the same account.",
    )
  }

  if (restarted?.length) {
    lines.push(
      `Slot ${restarted.join(", ")} holds a different character than last time — its relics were left out of the comparison rather than counted as lost.`,
    )
  }

  if (lines.length === 0) return null
  return (
    <Alert>
      <Info className="h-4 w-4" />
      <AlertTitle>
        {compared ? "Comparison caveat" : "Not compared to your last save"}
      </AlertTitle>
      <AlertDescription>
        <div className="mt-1 space-y-1 text-sm">
          {lines.map((l) => (
            <p key={l}>{l}</p>
          ))}
        </div>
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

  // Background upload + re-optimization job (authenticated). Lives in a module
  // store (lib/optimizeJobs) so it survives navigation away from this page; here
  // we just read it to render in-page progress and results.
  const job = useOptimizeJob()
  const streamActive = job?.status === "parsing" || job?.status === "optimizing"
  const streamProgress = job?.progress ?? null
  const streamError = job?.status === "error" ? (job.error ?? null) : null

  // The navbar tracker auto-clears a finished job a few seconds after it lands
  // (OptimizeTrackerButton), and dismissing it from the Sheet clears it outright
  // — either would yank this page's results away mid-read. So latch the result
  // here on completion: the job store owns the *live* stream, this page owns
  // what it last showed. Cleared when a new upload starts.
  const [streamResult, setStreamResult] = useState<StreamUploadResult | null>(
    null,
  )
  const doneResult = job?.status === "done" ? (job.result ?? null) : null
  useEffect(() => {
    if (doneResult) setStreamResult(doneResult)
  }, [doneResult])

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

  // Confirmation gate: a pending upload paused on staged changes it would
  // discard. Staged edits are keyed by slot against the currently loaded save,
  // so a replacement file can never inherit them.
  const [gate, setGate] = useState<{
    file: File
    summaries: SlotSummary[]
    hasMints: boolean
  } | null>(null)

  /** The unconditional upload path: replace the working file, drop the (now
   *  resolved) staged diff, back up the original, and start the upload. */
  function proceedUpload(file: File) {
    // Drop the previous upload's latched results — they describe the save this
    // one replaces.
    setStreamResult(null)
    setUploadResult(null)
    // Keep the original file in-session so the inventory page can export an
    // edited copy without re-uploading (raw saves are never persisted).
    rememberSaveFile(file)
    // A new save replaces the file the pending diff was computed against, so the
    // old edits (keyed by slot index) would silently re-attach to a different
    // save. Discard them — they can't be validly applied to the new file.
    clearAll()
    // Also stash a durable, in-browser backup of the pristine original so the
    // user can recover it later if their save ever gets corrupted.
    void storeOriginalBackup(file)
    showSuccessToast(
      "Original save backed up in this browser — you can download it anytime from the Upload page.",
    )
    if (isLoggedIn()) {
      void startUpload(file)
    } else {
      uploadMutation.mutate(file)
    }
  }

  /**
   * Route the picked file through the confirmation gate: with staged edits
   * present, warn before the upload wipes them; otherwise upload straight away.
   */
  function routeThroughGate(file: File) {
    const pending = readAll()
    if (Object.keys(pending).length === 0) {
      proceedUpload(file)
      return
    }
    const summaries = summarizePending(pending)
    setGate({
      file,
      summaries,
      hasMints: summaries.some((r) => r.mints > 0),
    })
  }

  function handleFile(file: File) {
    const name = file.name.toLowerCase()
    if (!name.endsWith(".sl2") && !name.endsWith(".dat")) {
      showErrorToast("Please upload a .sl2 (PC) or memory.dat (PS4) file.")
      return
    }
    routeThroughGate(file)
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files[0]
    if (file) handleFile(file)
  }

  function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    // Clear the input so cancelling at the gate lets the same file re-fire.
    e.target.value = ""
    if (file) handleFile(file)
  }

  const isPending = streamActive || uploadMutation.isPending
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

      <UploadGateDialog
        open={gate !== null}
        summaries={gate?.summaries ?? []}
        hasMints={gate?.hasMints ?? false}
        onDiscard={() => {
          const file = gate?.file
          setGate(null)
          if (file) proceedUpload(file)
        }}
        onCancel={() => setGate(null)}
      />

      <SaveStatusBanner />

      <OriginalBackupCard />

      {/* Drop zone */}
      {/* biome-ignore lint/a11y/useSemanticElements: drop zone wraps a hidden file input (a <button> can't contain it); role/tabIndex/onKeyDown give it full keyboard a11y */}
      <div
        role="button"
        tabIndex={0}
        aria-label="Upload a save file"
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => fileInputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault()
            fileInputRef.current?.click()
          }
        }}
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
          <ComparisonNote comparison={streamResult.comparison} />
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
