import { useCallback, useState } from "react"

/**
 * useState persisted to localStorage (per-browser UI preferences such as a
 * collapsed panel). Falls back to plain state when storage is unavailable.
 */
export function useStoredState<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = window.localStorage.getItem(key)
      return raw === null ? initial : (JSON.parse(raw) as T)
    } catch {
      return initial
    }
  })

  const update = useCallback(
    (next: T) => {
      setValue(next)
      try {
        window.localStorage.setItem(key, JSON.stringify(next))
      } catch {
        // Storage unavailable — keep the in-memory value.
      }
    },
    [key],
  )

  return [value, update] as const
}
