import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiRequest } from "@/lib/api"

export interface KillSwitchState {
  control_key: "global_kill_switch"
  enabled: boolean
  reason: string | null
  changed_by: string
  changed_at: string
  redis_published: boolean | null
}

export interface FundamentalControlState {
  control_key: string
  enabled: boolean
  paused: boolean
  reason: string | null
  changed_by: string
  changed_at: string
  redis_published: boolean | null
}

export interface FundamentalControlsState {
  processing: FundamentalControlState
  ai: FundamentalControlState
}

export const systemControlKeys = {
  all: ["system-controls"] as const,
  killSwitch: () => [...systemControlKeys.all, "global-kill-switch"] as const,
  fundamentals: () => [...systemControlKeys.all, "fundamentals"] as const,
}

export function useFundamentalControls() {
  return useQuery({
    queryKey: systemControlKeys.fundamentals(),
    queryFn: () => apiRequest<FundamentalControlsState>("/system/fundamentals-controls"),
    staleTime: 2_000,
    refetchInterval: 5_000,
    retry: 1,
  })
}

export function useSetFundamentalControl() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ control, paused, reason }: { control: "processing" | "ai"; paused: boolean; reason: string }) =>
      apiRequest<FundamentalControlState>(`/system/fundamentals-controls/${control}`, {
        method: "PUT",
        body: JSON.stringify({ paused, reason }),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: systemControlKeys.fundamentals() })
    },
  })
}

export function useKillSwitch() {
  return useQuery({
    queryKey: systemControlKeys.killSwitch(),
    queryFn: () => apiRequest<KillSwitchState>("/system/kill-switch"),
    staleTime: 2_000,
    refetchInterval: 5_000,
  })
}

export function useSetKillSwitch() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      enabled,
      reason,
    }: {
      enabled: boolean
      reason: string
    }) =>
      apiRequest<KillSwitchState>("/system/kill-switch", {
        method: "PUT",
        body: JSON.stringify({ enabled, reason }),
      }),
    onSuccess: (state) => {
      queryClient.setQueryData(systemControlKeys.killSwitch(), state)
    },
  })
}
