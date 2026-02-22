/**
 * Shared test utilities for component and hook tests.
 *
 * renderWithProviders wraps children in the providers required by most
 * components: React Query and a minimal TanStack Router memory router.
 */
import type { ReactElement } from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, type RenderOptions } from "@testing-library/react"

export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0 },
      mutations: { retry: false },
    },
  })
}

interface WrapperOptions extends Omit<RenderOptions, "wrapper"> {
  queryClient?: QueryClient
}

/** Render with a fresh QueryClient per call (no state bleed between tests). */
export function renderWithProviders(
  ui: ReactElement,
  { queryClient, ...options }: WrapperOptions = {},
) {
  const client = queryClient ?? createTestQueryClient()

  function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )
  }

  return render(ui, { wrapper: Wrapper, ...options })
}
