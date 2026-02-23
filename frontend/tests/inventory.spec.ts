import { expect, test } from "@playwright/test"
import { logInUser } from "./utils/user"
import { randomEmail, randomPassword } from "./utils/random"
import { createUser } from "./utils/privateApi"

test.describe("Inventory page — anonymous (no save loaded)", () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test("shows correct heading and description", async ({ page }) => {
    await page.goto("/inventory")
    await expect(page.getByRole("heading", { name: "Relic Inventory" })).toBeVisible()
    await expect(page.getByText("Browse relics from your save file.")).toBeVisible()
  })

  test("shows empty state with upload link pointing to /upload", async ({ page }) => {
    // Fresh anonymous context — parsedCharacters is empty, AnonInventory shows empty state.
    // Wait for toBeVisible first (effects API may take a few seconds), then check href.
    await page.goto("/inventory")
    await expect(page.getByText("No inventory loaded.")).toBeVisible()
    const link = page.getByRole("link", { name: "Upload a save file" })
    await expect(link).toBeVisible()
    await expect(link).toHaveAttribute("href", "/upload")
  })
})

test.describe("Inventory page — authenticated (no save uploaded)", () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  let email: string
  const password = randomPassword()

  test.beforeAll(async () => {
    email = randomEmail()
    await createUser({ email, password })
  })

  test.beforeEach(async ({ page }) => {
    await logInUser(page, email, password)
    await page.goto("/inventory")
  })

  test("shows empty state with link to upload when no characters exist", async ({ page }) => {
    await expect(page.getByText("No characters found.")).toBeVisible()
    await expect(page.getByRole("link", { name: "Upload a save file" })).toBeVisible()
  })

  test("shows page heading", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Relic Inventory" })).toBeVisible()
  })
})
