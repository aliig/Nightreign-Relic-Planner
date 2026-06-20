import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query"

import { ApiError } from "@/client"

/**
 * The app's single QueryClient.
 *
 * Lives here (rather than inline in main.tsx) so non-component code — e.g. the
 * background optimize-job store in lib/optimizeJobs.ts — can invalidate queries
 * after work that finished while no component was mounted to do it.
 */
const handleApiError = (error: Error) => {
  if (error instanceof ApiError && [401, 403].includes(error.status)) {
    const hadToken = localStorage.getItem("access_token") !== null
    localStorage.removeItem("access_token")
    if (hadToken) {
      window.location.href = "/login"
    }
  }
}

export const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: handleApiError,
  }),
  mutationCache: new MutationCache({
    onError: handleApiError,
  }),
})
