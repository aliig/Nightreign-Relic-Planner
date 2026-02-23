import path from "node:path"
import { fileURLToPath } from "node:url"
import { expect, test } from "@playwright/test"

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

// All upload tests run without auth (the upload page works for anonymous users)
test.use({ storageState: { cookies: [], origins: [] } })

test("Upload page shows correct heading and description", async ({ page }) => {
  await page.goto("/upload")
  await expect(page.getByRole("heading", { name: "Upload Save File" })).toBeVisible()
  await expect(
    page.getByText("Import your PC (.sl2) or PS4 (memory.dat) save to load your relic inventory."),
  ).toBeVisible()
})

test("Drop zone is visible with correct instructions", async ({ page }) => {
  await page.goto("/upload")
  await expect(page.getByText("Drop your save file here")).toBeVisible()
  await expect(page.getByText("or click to browse")).toBeVisible()
})

test("Drop zone shows accepted file format hints", async ({ page }) => {
  await page.goto("/upload")
  await expect(page.getByText(".sl2 (PC) · memory.dat (PS4)")).toBeVisible()
  // The path hint contains a backslash-separated Windows path — match on the filename
  await expect(page.getByText(/NR0000\.sl2/)).toBeVisible()
})

test("File input accepts .sl2 and .dat files", async ({ page }) => {
  await page.goto("/upload")
  const input = page.locator('input[type="file"]')
  await expect(input).toHaveAttribute("accept", ".sl2,.dat")
})

test("Uploading a non-save file shows a format error", async ({ page }) => {
  await page.goto("/upload")

  // Create a temp txt file and try uploading it via the hidden input
  const tmpFile = path.join(__dirname, "fixtures", "bad-file.txt")
  const input = page.locator('input[type="file"]')
  await input.setInputFiles(tmpFile)

  await expect(
    page.getByText("Please upload a .sl2 (PC) or memory.dat (PS4) file."),
  ).toBeVisible()
})
