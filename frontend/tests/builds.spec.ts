import { expect, test } from "@playwright/test"
import { logInUser } from "./utils/user"
import { randomEmail, randomPassword } from "./utils/random"
import { createUser } from "./utils/privateApi"

// ---------------------------------------------------------------------------
// Anonymous builds (localStorage-backed)
// ---------------------------------------------------------------------------

test.describe("Builds page — anonymous", () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test.beforeEach(async ({ page }) => {
    // Start with clean localStorage so no stale builds bleed between tests
    await page.goto("/builds")
    await page.evaluate(() => localStorage.clear())
    await page.reload()
  })

  test("shows Builds heading and description", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Builds" })).toBeVisible()
    await expect(
      page.getByText("Create build definitions to drive the optimizer."),
    ).toBeVisible()
  })

  test("shows New Build button", async ({ page }) => {
    await expect(page.getByRole("button", { name: "New Build" })).toBeVisible()
  })

  test("shows browser-storage notice with sign-in link", async ({ page }) => {
    // Scope to the notice paragraph to avoid matching the sidebar "Sign In" link
    const notice = page.locator("p", { hasText: "Builds are stored in your browser." })
    await expect(notice).toBeVisible()
    await expect(notice.getByRole("link", { name: "Sign in" })).toBeVisible()
  })

  test("shows empty state when no builds exist", async ({ page }) => {
    await expect(
      page.getByText("No builds yet. Create one to get started."),
    ).toBeVisible()
  })

  test("creates a new build and shows the card", async ({ page }) => {
    await page.getByRole("button", { name: "New Build" }).click()

    const dialog = page.getByRole("dialog")
    await expect(dialog.getByText("Create Build")).toBeVisible()

    await dialog.getByLabel("Name").fill("Fire Wylder")
    // Character defaults to Wylder — no need to change
    await dialog.getByRole("button", { name: "Create" }).click()

    await expect(page.getByRole("dialog")).not.toBeVisible()
    // Verify the card exists via its aria-labelled delete button
    await expect(page.getByRole("button", { name: 'Delete "Fire Wylder"' })).toBeVisible()
  })

  test("build name is required", async ({ page }) => {
    await page.getByRole("button", { name: "New Build" }).click()

    const dialog = page.getByRole("dialog")
    // Leave name empty and submit — react-hook-form validates on submit by default
    await dialog.getByLabel("Name").fill("")
    await dialog.getByRole("button", { name: "Create" }).click()

    await expect(dialog.getByText("Name is required")).toBeVisible()
  })

  test("deletes a build after confirming", async ({ page }) => {
    // Create a build first
    await page.getByRole("button", { name: "New Build" }).click()
    await page.getByRole("dialog").getByLabel("Name").fill("Build To Delete")
    await page.getByRole("dialog").getByRole("button", { name: "Create" }).click()
    await expect(page.getByRole("dialog")).not.toBeVisible()
    // Verify the card exists via its aria-labelled delete button
    await expect(page.getByRole("button", { name: 'Delete "Build To Delete"' })).toBeVisible()

    // Click the delete icon button
    await page.getByRole("button", { name: 'Delete "Build To Delete"' }).click()

    // Confirm deletion in the dialog
    const confirmDialog = page.getByRole("dialog")
    await expect(confirmDialog.getByText(/Delete "Build To Delete"\?/)).toBeVisible()
    await confirmDialog.getByRole("button", { name: "Delete" }).click()

    await expect(page.getByRole("button", { name: 'Delete "Build To Delete"' })).not.toBeVisible()
  })

  test("all character options are available in new build dialog", async ({ page }) => {
    await page.getByRole("button", { name: "New Build" }).click()

    const dialog = page.getByRole("dialog")
    // Open the character select
    await dialog.getByRole("combobox").click()

    const expectedCharacters = [
      "Wylder", "Guardian", "Ironeye", "Duchess", "Raider",
      "Revenant", "Recluse", "Executor", "Scholar", "Undertaker",
    ]
    for (const char of expectedCharacters) {
      await expect(page.getByRole("option", { name: char })).toBeVisible()
    }
  })
})

// ---------------------------------------------------------------------------
// Authenticated builds (API-backed)
// ---------------------------------------------------------------------------

test.describe("Builds page — authenticated", () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  let email: string
  const password = randomPassword()

  test.beforeAll(async () => {
    email = randomEmail()
    await createUser({ email, password })
  })

  test.beforeEach(async ({ page }) => {
    await logInUser(page, email, password)
    await page.goto("/builds")
  })

  test("shows Builds heading when logged in", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Builds" })).toBeVisible()
  })

  test("shows New Build button when logged in", async ({ page }) => {
    await expect(page.getByRole("button", { name: "New Build" })).toBeVisible()
  })

  test("shows empty state when user has no builds", async ({ page }) => {
    await expect(
      page.getByText("No builds yet. Create one to get started."),
    ).toBeVisible()
  })

  test("creates and then deletes a build via the API", async ({ page }) => {
    // Create
    await page.getByRole("button", { name: "New Build" }).click()
    await page.getByRole("dialog").getByLabel("Name").fill("Auth Build")
    await page.getByRole("dialog").getByRole("button", { name: "Create" }).click()
    await expect(page.getByRole("dialog")).not.toBeVisible()
    await expect(page.getByRole("button", { name: 'Delete "Auth Build"' })).toBeVisible()

    // Delete
    await page.getByRole("button", { name: 'Delete "Auth Build"' }).click()
    const confirmDialog = page.getByRole("dialog")
    await expect(confirmDialog.getByText(/Delete "Auth Build"\?/)).toBeVisible()
    await confirmDialog.getByRole("button", { name: "Delete" }).click()

    await expect(page.getByRole("button", { name: 'Delete "Auth Build"' })).not.toBeVisible()
  })
})
