// Note: the `PrivateService` is only available when generating the client
// for local environments
import { OpenAPI, PrivateService } from "../../src/client"

// VITE_API_URL is empty in frontend/.env because the browser uses Vite's
// dev-server proxy. In Node.js (Playwright), we need the real backend URL.
OpenAPI.BASE = process.env.VITE_API_URL || "http://localhost:8000"

export const createUser = async ({
  email,
  password,
}: {
  email: string
  password: string
}) => {
  return await PrivateService.createUser({
    requestBody: {
      email,
      password,
      is_verified: true,
      full_name: "Test User",
    },
  })
}
