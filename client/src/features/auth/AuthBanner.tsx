import {
  AlertTriangleIcon,
  LogInIcon,
  RefreshCwIcon,
  SendIcon,
  ShieldCheckIcon,
} from "lucide-react"

import {
  Alert,
  AlertDescription,
  AlertTitle,
} from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  useAuthEvents,
  useAuthStatus,
  useManualRefreshToken,
  useRunTotpLogin,
  useSendTelegramLoginLink,
  useStartFyersLogin,
  useVerifyFyersSession,
} from "@/features/auth/api"

export function AuthBanner() {
  const authStatus = useAuthStatus()
  const authEvents = useAuthEvents(authStatus.data?.healthy === false)
  const startLogin = useStartFyersLogin()
  const manualRefresh = useManualRefreshToken()
  const sendLoginLink = useSendTelegramLoginLink()
  const verifySession = useVerifyFyersSession()
  const totpLogin = useRunTotpLogin()
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
  const canRefresh = Boolean(
    authStatus.data?.has_refresh_token && authStatus.data?.has_pin
  )
  const canSendLink = Boolean(authStatus.data?.telegram_enabled)
  const canTotpLogin = Boolean(
    authStatus.data?.headless_login_enabled &&
      authStatus.data?.headless_login_configured
  )
  const cutoff = authStatus.data?.session_cutoff_ist

  const reason =
    callbackError
      ? `Fyers login failed: ${callbackError}`
      : authStatus.error instanceof Error
      ? authStatus.error.message
      : authStatus.data?.reason === "expired"
        ? `The Fyers session ended at the ${cutoff ?? "06:30"} IST daily cutoff.`
        : authStatus.data?.reason === "no_token"
          ? "Fyers has not been connected yet."
          : latestFailure
            ? `Fyers reported ${latestFailure.event_type.replaceAll("_", " ")}.`
            : "Fyers authentication is unavailable."

  return (
    <Alert className="rounded-none border-x-0 border-t-0" variant="destructive">
      <AlertTriangleIcon aria-hidden="true" />
      <AlertTitle>Market data authentication required</AlertTitle>
      <AlertDescription className="flex flex-col gap-3">
        <span>
          {reason} New entries stay blocked until the session is restored;
          sync, scanner refresh, and broker workers may be paused.
        </span>
        <div className="flex flex-wrap items-center gap-2">
          {canSendLink && (
            <Button
              disabled={sendLoginLink.isPending}
              onClick={() => sendLoginLink.mutate()}
              size="sm"
              type="button"
              variant="outline"
            >
              <SendIcon data-icon="inline-start" />
              {sendLoginLink.isSuccess
                ? "Login link sent to Telegram"
                : "Send login link to Telegram"}
            </Button>
          )}
          {canTotpLogin && (
            <Button
              disabled={totpLogin.isPending}
              onClick={() => totpLogin.mutate()}
              size="sm"
              type="button"
              variant="outline"
            >
              <ShieldCheckIcon data-icon="inline-start" />
              Run TOTP login
            </Button>
          )}
          {canRefresh && (
            <Button
              disabled={manualRefresh.isPending}
              onClick={() => manualRefresh.mutate()}
              size="sm"
              type="button"
              variant="outline"
            >
              <RefreshCwIcon className={manualRefresh.isPending ? "animate-spin" : ""} data-icon="inline-start" />
              Refresh Session
            </Button>
          )}
          <Button
            disabled={verifySession.isPending}
            onClick={() => verifySession.mutate()}
            size="sm"
            type="button"
            variant="outline"
          >
            <ShieldCheckIcon data-icon="inline-start" />
            Test connection
          </Button>
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
        </div>
        {(sendLoginLink.isError || totpLogin.isError || verifySession.isError) && (
          <span className="text-xs">
            {(
              (sendLoginLink.error ??
                totpLogin.error ??
                verifySession.error) as Error
            )?.message ?? "Action failed."}
          </span>
        )}
        {verifySession.data && (
          <span className="text-xs">
            {verifySession.data.verified
              ? `Verified ${verifySession.data.identity ?? "session"} · ends ${verifySession.data.expires_at ?? "?"}`
              : `Broker check failed (${verifySession.data.reason ?? "unknown"}).`}
          </span>
        )}
      </AlertDescription>
    </Alert>
  )
}
