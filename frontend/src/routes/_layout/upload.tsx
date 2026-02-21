import { createFileRoute, useNavigate } from "@tanstack/react-router"
import { useRef, useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Upload, User2, AlertCircle } from "lucide-react"

import { SavesService } from "@/client"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { useCustomToast } from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/upload")({
  component: UploadPage,
  head: () => ({
    meta: [{ title: "Upload Save - Nightreign Relic Planner" }],
  }),
})

function UploadPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showSuccessToast } = useCustomToast()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [uploadResult, setUploadResult] = useState<Awaited<ReturnType<typeof SavesService.uploadSave>> | null>(null)

  const uploadMutation = useMutation({
    mutationFn: (file: File) =>
      SavesService.uploadSave({ formData: { file } }),
    onSuccess: (data) => {
      setUploadResult(data)
      queryClient.invalidateQueries({ queryKey: ["characters"] })
      if (data.persisted) {
        showSuccessToast(
          `Save imported — ${data.character_count} character${data.character_count !== 1 ? "s" : ""} found.`,
        )
      }
    },
    onError: (err) => handleError(err),
  })

  function handleFile(file: File) {
    const name = file.name.toLowerCase()
    if (!name.endsWith(".sl2") && !name.endsWith(".dat")) {
      handleError(new Error("Please upload a .sl2 (PC) or memory.dat (PS4) file."))
      return
    }
    uploadMutation.mutate(file)
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

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Upload Save File</h1>
        <p className="text-muted-foreground mt-1">
          Import your PC (.sl2) or PS4 (memory.dat) save to load your relic inventory.
        </p>
      </div>

      {/* Drop zone */}
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
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
        <p className="text-xs text-muted-foreground mt-3">.sl2 (PC) · memory.dat (PS4)</p>
        <input
          ref={fileInputRef}
          type="file"
          accept=".sl2,.dat"
          className="hidden"
          onChange={onFileChange}
        />
      </div>

      {uploadMutation.isPending && (
        <p className="text-sm text-muted-foreground animate-pulse">
          Parsing save file…
        </p>
      )}

      {uploadMutation.isError && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>
            {(uploadMutation.error as Error).message}
          </AlertDescription>
        </Alert>
      )}

      {/* Character list */}
      {uploadResult && (
        <div className="space-y-3">
          <h2 className="text-lg font-medium">
            Found {uploadResult.character_count} character{uploadResult.character_count !== 1 ? "s" : ""}
          </h2>
          <div className="grid gap-3 sm:grid-cols-2">
            {uploadResult.characters.map((char) => (
              <Card
                key={char.slot_index}
                className="cursor-pointer hover:border-primary/50 transition-colors"
                onClick={() => {
                  // Store selected character in sessionStorage for the inventory page
                  sessionStorage.setItem("selectedCharacter", JSON.stringify(char))
                  navigate({ to: "/inventory" })
                }}
              >
                <CardHeader className="pb-2">
                  <div className="flex items-center gap-2">
                    <User2 className="h-4 w-4 text-muted-foreground" />
                    <CardTitle className="text-base">{char.name}</CardTitle>
                  </div>
                </CardHeader>
                <CardContent>
                  <CardDescription>
                    Slot {char.slot_index} · {char.relic_count} relic{char.relic_count !== 1 ? "s" : ""}
                  </CardDescription>
                </CardContent>
              </Card>
            ))}
          </div>
          {!uploadResult.persisted && (
            <Alert>
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>
                You're not logged in — your inventory won't be saved between sessions.{" "}
                <a href="/login" className="underline">Sign in</a> to persist your data.
              </AlertDescription>
            </Alert>
          )}
        </div>
      )}
    </div>
  )
}
