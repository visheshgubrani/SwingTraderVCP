import { useState, type FormEvent } from "react"
import { Eye, EyeOff, Lock, ShieldCheck, AlertCircle, Loader2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { useAppAuth } from "./AuthContext"

export function LoginPage() {
  const { login, isLoading, error } = useAppAuth()
  const [password, setPassword] = useState("")
  const [showPassword, setShowPassword] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!password.trim()) {
      setLocalError("Password is required.")
      return
    }
    setLocalError(null)
    try {
      await login(password)
    } catch {
      // Error handled by AuthContext
    }
  }

  const activeError = localError || error

  return (
    <div className="flex min-h-screen w-screen items-center justify-center bg-background px-4 text-foreground">
      <div className="relative w-full max-w-md overflow-hidden rounded-xl border border-border/80 bg-card/60 p-8 shadow-2xl backdrop-blur-md">
        {/* Subtle top accent line */}
        <div className="absolute inset-x-0 top-0 h-[2px] bg-gradient-to-r from-emerald-500/0 via-emerald-500/80 to-emerald-500/0" />

        <div className="mb-6 text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-lg border border-border bg-muted/40 shadow-inner">
            <ShieldCheck className="h-6 w-6 text-emerald-400" />
          </div>
          <h1 className="text-xl font-bold tracking-tight text-foreground">
            SwingTraderVCP
          </h1>
          <p className="mt-1 text-xs text-muted-foreground">
            Personal Trading Workstation • Secure Authentication
          </p>
        </div>

        {activeError && (
          <Alert variant="destructive" className="mb-5 py-2.5 text-xs">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{activeError}</AlertDescription>
          </Alert>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <label
              htmlFor="app-password"
              className="text-xs font-medium text-muted-foreground"
            >
              Workstation Password
            </label>
            <div className="relative">
              <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-muted-foreground">
                <Lock className="h-4 w-4" />
              </div>
              <Input
                id="app-password"
                type={showPassword ? "text" : "password"}
                autoFocus
                placeholder="Enter password"
                value={password}
                onChange={(e) => {
                  setPassword(e.target.value)
                  if (localError) setLocalError(null)
                }}
                disabled={isLoading}
                className="pl-9 pr-10 font-mono text-sm"
              />
              <button
                type="button"
                tabIndex={-1}
                onClick={() => setShowPassword((prev) => !prev)}
                className="absolute inset-y-0 right-0 flex items-center pr-3 text-muted-foreground hover:text-foreground"
              >
                {showPassword ? (
                  <EyeOff className="h-4 w-4" />
                ) : (
                  <Eye className="h-4 w-4" />
                )}
              </button>
            </div>
          </div>

          <Button
            type="submit"
            disabled={isLoading || !password}
            className="w-full font-medium"
          >
            {isLoading ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Authenticating...
              </>
            ) : (
              "Sign In to Workstation"
            )}
          </Button>
        </form>

        <div className="mt-6 text-center">
          <p className="text-[11px] text-muted-foreground/70">
            Protected personal money-path & deterministic execution system.
          </p>
        </div>
      </div>
    </div>
  )
}
