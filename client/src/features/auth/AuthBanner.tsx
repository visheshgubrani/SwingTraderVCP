import { AlertTriangleIcon, LogInIcon } from "lucide-react"

import {
  Alert,
  AlertDescription,
  AlertTitle,
} from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  useAuthEvents,
  useAuthStatus,
  useStartFyersLogin,
} from "@/features/auth/api"

export function AuthBanner() {
  const authStatus = useAuthStatus()
  const authEvents = useAuthEvents(authStatus.data?.healthy === false)
  const startLogin = useStartFyersLogin()
  const callbackError = new URLSearchParams(window.location.search).get("error")

  if (
    authStatus.isLoading ||
    (authStatus.data?.healthy && !callbackError)
  ) {
    return null
  }

  const latestFailure = authEvents.data?.find(
    (event) =>
      event.severity === "critical" ||
      event.severity === "error" ||
      event.severity === "warning",
  )
  const reason =
    callbackError
      ? `Fyers login failed: ${callbackError}`
      : authStatus.error instanceof Error
      ? authStatus.error.message
      : authStatus.data?.reason === "expired"
        ? "The Fyers token has expired."
        : authStatus.data?.reason === "no_token"
          ? "Fyers has not been connected yet."
          : latestFailure
            ? `Fyers reported ${latestFailure.event_type.replaceAll("_", " ")}.`
            : "Fyers authentication is unavailable."

  return (
    <Alert className="rounded-none border-x-0 border-t-0" variant="destructive">
      <AlertTriangleIcon aria-hidden="true" />
      <AlertTitle>Market data authentication required</AlertTitle>
      <AlertDescription className="flex items-center justify-between gap-4">
        <span>{reason} Sync, scanner refresh, and broker workers may be paused.</span>
        <Button
          disabled={startLogin.isPending}
          onClick={() => startLogin.mutate()}
          size="sm"
          type="button"
          variant="outline"
        >
          <LogInIcon data-icon="inline-start" />
          Login to Fyers
        </Button>
      </AlertDescription>
    </Alert>
  )
}
