import { useCallback, useMemo } from "react"
import { useSearchParams } from "react-router"

export type UrlStatePatch<T extends Record<string, string>> = Partial<Record<keyof T, string | null>>

/**
 * Page view state (tab, filters, sort, selection) kept in the query string so
 * it survives navigating away and back, and so back/forward behave.
 *
 * Keys equal to their default are omitted from the URL. Updates replace the
 * history entry by default; pass `{ replace: false }` for steps the back
 * button should undo (e.g. opening a detail drawer).
 */
export function useUrlState<T extends Record<string, string>>(defaults: T) {
  const [searchParams, setSearchParams] = useSearchParams()

  const state = useMemo(() => {
    const next = { ...defaults }
    for (const key of Object.keys(defaults) as (keyof T)[]) {
      const value = searchParams.get(key as string)
      if (value !== null) next[key] = value as T[keyof T]
    }
    return next
    // defaults is a literal at the call site; its values are what matter.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, JSON.stringify(defaults)])

  const setState = useCallback(
    (patch: UrlStatePatch<T>, { replace = true }: { replace?: boolean } = {}) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          for (const [key, value] of Object.entries(patch)) {
            if (value === null || value === undefined || value === defaults[key]) next.delete(key)
            else next.set(key, value)
          }
          return next
        },
        { replace },
      )
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [setSearchParams, JSON.stringify(defaults)],
  )

  return [state, setState] as const
}
