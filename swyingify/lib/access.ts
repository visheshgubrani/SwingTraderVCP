import { queryOptions } from "@tanstack/react-query"

import type { AccessContext } from "@/lib/access-types"

export const accessKeys = {
  all: ["access"] as const,
  current: () => [...accessKeys.all, "current"] as const,
}

export async function fetchAccess(): Promise<AccessContext> {
  const response = await fetch("/api/access", {
    headers: { Accept: "application/json" },
    cache: "no-store",
  })
  if (!response.ok) {
    throw new Error(`Access lookup failed (${response.status})`)
  }
  return response.json() as Promise<AccessContext>
}

export function accessQuery() {
  return queryOptions({
    queryKey: accessKeys.current(),
    queryFn: fetchAccess,
    staleTime: 30_000,
    gcTime: 10 * 60 * 1000,
  })
}
