import { type ReactElement, type ReactNode } from "react"
import { render, type RenderOptions } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter, type MemoryRouterProps } from "react-router"

export function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
      mutations: {
        retry: false,
      },
    },
  })
}

interface WrapperProps {
  children: ReactNode
}

interface CustomRenderOptions extends Omit<RenderOptions, "wrapper"> {
  queryClient?: QueryClient
  routerInitialEntries?: MemoryRouterProps["initialEntries"]
}

export function renderWithProviders(
  ui: ReactElement,
  options?: CustomRenderOptions
) {
  const queryClient = options?.queryClient ?? createTestQueryClient()
  const initialEntries = options?.routerInitialEntries ?? ["/"]

  function AllTheProviders({ children }: WrapperProps) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={initialEntries}>
          {children}
        </MemoryRouter>
      </QueryClientProvider>
    )
  }

  return {
    user: userEvent.setup(),
    ...render(ui, { wrapper: AllTheProviders, ...options }),
    queryClient,
  }
}

export * from "@testing-library/react"
export { userEvent }
