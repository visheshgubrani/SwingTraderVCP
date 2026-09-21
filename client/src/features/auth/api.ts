import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiRequest } from "@/lib/api"

const FYERS_AUTH_STATE_KEY = "fyers_auth_state"

export interface AuthStatus {
  authenticated: boolean
  healthy: boolean
  reason?: "no_token" | "expired" | string
  expires_at?: string
}

export interface AuthEvent {
  event_ts: string
  severity: "debug" | "info" | "warning" | "error" | "critical"
  event_type: string
  payload: Record<string, unknown> | string | null
}

interface AuthUrlResponse {
  url: string
  state: string
}

interface AuthCallbackResponse {
  status: "ok"
  message: string
}

export const authKeys = {
  all: ["auth"] as const,
  status: () => [...authKeys.all, "status"] as const,
  events: () => [...authKeys.all, "events"] as const,
}

export function getStoredFyersAuthState() {
  return window.sessionStorage.getItem(FYERS_AUTH_STATE_KEY)
}

export function clearStoredFyersAuthState() {
  window.sessionStorage.removeItem(FYERS_AUTH_STATE_KEY)
}

export function useAuthStatus() {
  return useQuery({
    queryKey: authKeys.status(),
    queryFn: () => apiRequest<AuthStatus>("/auth/status"),
    staleTime: 5_000,
    refetchInterval: 10_000,
    retry: false,
  })
}

export function useAuthEvents(enabled = true) {
  return useQuery({
    queryKey: authKeys.events(),
    queryFn: () => apiRequest<AuthEvent[]>("/auth/events?limit=10"),
    enabled,
    staleTime: 10_000,
    refetchInterval: enabled ? 15_000 : false,
    retry: false,
  })
}

export function useStartFyersLogin() {
  return useMutation({
    mutationFn: () => apiRequest<AuthUrlResponse>("/auth/url"),
    onSuccess: ({ state, url }) => {
      window.sessionStorage.setItem(FYERS_AUTH_STATE_KEY, state)
      window.location.assign(url)
    },
  })
}

export function useExchangeFyersCode() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ code, state }: { code: string; state: string }) =>
      apiRequest<AuthCallbackResponse>("/auth/callback", {
        method: "POST",
        body: JSON.stringify({ code, state }),
      }),
    onSuccess: () => {
      clearStoredFyersAuthState()
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: authKeys.status() }),
        queryClient.invalidateQueries({ queryKey: authKeys.events() }),
      ])
    },
  })
}
