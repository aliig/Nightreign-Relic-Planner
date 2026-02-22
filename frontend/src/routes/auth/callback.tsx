import { createFileRoute, redirect } from "@tanstack/react-router"

export const Route = createFileRoute("/auth/callback")({
  beforeLoad: () => {
    // The backend redirects here with the JWT in the URL fragment:
    //   /auth/callback#token=<jwt>
    // Fragments are never sent to the server, so the token stays client-side.
    const hash = window.location.hash.slice(1) // strip leading '#'
    const params = new URLSearchParams(hash)
    const token = params.get("token")
    if (token) {
      localStorage.setItem("access_token", token)
    }
    throw redirect({ to: "/" })
  },
  component: () => null,
})
