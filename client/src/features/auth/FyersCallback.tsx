import { useEffect, useRef, useState } from "react"
import {
  AlertCircleIcon,
  CheckCircle2Icon,
  LogInIcon,
} from "lucide-react"

import {
  Alert,
  AlertDescription,
  AlertTitle,
} from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import {
  clearStoredFyersAuthState,
  getStoredFyersAuthState,
  useExchangeFyersCode,
} from "@/features/auth/api"

type CallbackState = "loading" | "success" | "error"

export function FyersCallback() {
  const [status, setStatus] = useState<CallbackState>("loading")
  const [localError, setLocalError] = useState<string | null>(null)
  const started = useRef(false)
  const exchangeCode = useExchangeFyersCode()

  useEffect(() => {
    if (started.current) return
    started.current = true

    const params = new URLSearchParams(window.location.search)
    const code = params.get("auth_code") ?? params.get("code")
    const returnedState = params.get("state")
    const expectedState = getStoredFyersAuthState()
    const brokerStatus = params.get("s")

    if (brokerStatus && brokerStatus !== "ok") {
      setStatus("error")
      setLocalError(
        params.get("message") ?? "Fyers rejected the authentication request.",
      )
      return
    }
    if (!code) {
      setStatus("error")
      setLocalError("The Fyers callback did not include an authorization code.")
      return
    }
    if (!expectedState || !returnedState || expectedState !== returnedState) {
      clearStoredFyersAuthState()
      setStatus("error")
      setLocalError(
        "The authentication state did not match. Start a new Fyers login from the dashboard.",
      )
      return
    }

    exchangeCode.mutate(
      { code, state: returnedState },
      {
        onSuccess: () => {
          window.history.replaceState({}, "", "/callback")
          setStatus("success")
          window.setTimeout(() => window.location.replace("/"), 1_000)
        },
        onError: () => setStatus("error"),
      },
    )
  }, [exchangeCode])

  const error =
    localError ??
    (exchangeCode.error instanceof Error
      ? exchangeCode.error.message
      : "Fyers authentication failed.")

  return (
    <main className="flex min-h-screen items-center justify-center bg-background p-6 text-foreground">
      <div className="flex w-full max-w-md flex-col gap-4 rounded-lg border bg-card p-6 shadow-lg">
        <div className="flex items-center gap-2">
          <LogInIcon aria-hidden="true" />
          <div>
            <h1 className="text-lg font-semibold">Fyers authentication</h1>
            <p className="text-sm text-muted-foreground">
              Completing the secure token exchange with the backend.
            </p>
          </div>
        </div>

        {status === "loading" && (
          <Alert>
            <Spinner />
            <AlertTitle>Connecting to Fyers</AlertTitle>
            <AlertDescription>
              Keep this page open while the authorization code is exchanged.
            </AlertDescription>
          </Alert>
        )}

        {status === "success" && (
          <Alert>
            <CheckCircle2Icon aria-hidden="true" />
            <AlertTitle>Fyers connected</AlertTitle>
            <AlertDescription>
              The token was saved securely. Returning to the dashboard…
            </AlertDescription>
          </Alert>
        )}

        {status === "error" && (
          <>
            <Alert variant="destructive">
              <AlertCircleIcon aria-hidden="true" />
              <AlertTitle>Authentication failed</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
            <Button onClick={() => window.location.replace("/")} type="button">
              Return to dashboard
            </Button>
          </>
        )}
      </div>
    </main>
  )
}
