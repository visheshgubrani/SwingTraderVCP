import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react"

export type ToastTone = "ok" | "warn" | "bad" | "info"

interface ToastItem {
  id: number
  tone: ToastTone
  title?: ReactNode
  text?: ReactNode
  mono?: string
}

interface ToastApi {
  toast: (tone: ToastTone, message: { title?: ReactNode; text?: ReactNode; mono?: string }) => void
}

const ToastContext = createContext<ToastApi | null>(null)

const TOAST_TTL = 4600

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const nextId = useRef(1)
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>())

  const dismiss = useCallback((id: number) => {
    setItems((prev) => prev.filter((item) => item.id !== id))
    const timer = timers.current.get(id)
    if (timer) clearTimeout(timer)
    timers.current.delete(id)
  }, [])

  const toast = useCallback<ToastApi["toast"]>(
    (tone, message) => {
      const id = nextId.current++
      setItems((prev) => [...prev.slice(-3), { id, tone, ...message }])
      const timer = setTimeout(() => dismiss(id), TOAST_TTL)
      timers.current.set(id, timer)
    },
    [dismiss],
  )

  const api = useMemo(() => ({ toast }), [toast])

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div aria-live="polite" className="toasts" role="status">
        {items.map((item) => (
          <div className={cnToast("toast", item.tone)} key={item.id}>
            <span aria-hidden="true" className="ti" />
            <span className="tmsg">
              {item.title && <b>{item.title}</b>}
              {item.text && <span> {item.text}</span>}
              {item.mono && <span className="mono">{item.mono}</span>}
            </span>
            <button
              aria-label="Dismiss notification"
              className="tcl"
              onClick={() => dismiss(item.id)}
              type="button"
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

function cnToast(base: string, tone: ToastTone): string {
  return `${base} ${tone === "ok" ? "ok" : tone === "bad" ? "bad" : tone === "warn" ? "warn" : "info"}`
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error("useToast must be used inside <ToastProvider>")
  return ctx
}
