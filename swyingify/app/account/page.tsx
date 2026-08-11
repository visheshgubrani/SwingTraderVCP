import type { Metadata } from "next"
import { headers } from "next/headers"
import Link from "next/link"
import { redirect } from "next/navigation"
import { ShieldCheckIcon } from "lucide-react"

import { AppHeader } from "@/components/app-header"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { resolveAccess } from "@/lib/entitlements"
import { buildPageMetadata } from "@/lib/seo/metadata"

export const metadata: Metadata = buildPageMetadata({
  title: "Account access",
  description: "Review your Swyingify account tier and scanner limits.",
  path: "/account",
  noIndex: true,
})

export const dynamic = "force-dynamic"

export default async function AccountPage() {
  const access = await resolveAccess(await headers())
  if (process.env.NODE_ENV === "production" && !access.isAuthenticated) {
    redirect("/sign-in?next=/account")
  }

  const tierLabel = {
    anonymous: "Anonymous",
    free: "Free",
    pro: "Pro",
    admin: "Admin",
    developer: "Developer",
  }[access.tier]

  return (
    <div className="min-h-screen bg-background">
      <AppHeader />
      <main className="mx-auto flex w-full max-w-5xl flex-col gap-6 px-4 py-10 sm:px-6 lg:px-8">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.18em] text-muted-foreground">Account</p>
          <h1 className="mt-3 font-display text-4xl font-semibold tracking-tight">Access ledger</h1>
          <p className="mt-3 text-muted-foreground">The exact tier and limits enforced by the production BFF.</p>
        </div>

        {access.isBypassed ? (
          <Alert>
            <ShieldCheckIcon />
            <AlertTitle>{tierLabel} bypass active</AlertTitle>
            <AlertDescription>
              {access.bypassReason === "development"
                ? "All paid capabilities are open outside production so feature development stays frictionless."
                : "This production admin account can exercise all paid capabilities without an active subscription."}
            </AlertDescription>
          </Alert>
        ) : null}

        <Card>
          <CardHeader>
            <CardTitle>{access.email ?? "Local development session"}</CardTitle>
            <CardDescription>Scanner access is evaluated on every protected request.</CardDescription>
            <CardAction>
              <Badge variant={access.tier === "pro" ? "default" : "outline"}>{tierLabel}</Badge>
            </CardAction>
          </CardHeader>
          <CardContent>
            <dl className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
              <div><dt className="text-xs text-muted-foreground">Strict scanner</dt><dd className="mt-1 font-medium">{access.features["scanner.strict"] ? "Open" : "Locked"}</dd></div>
              <div><dt className="text-xs text-muted-foreground">Custom runs</dt><dd className="mt-1 font-medium">{access.limits.variantRunsPerDay} / day</dd></div>
              <div><dt className="text-xs text-muted-foreground">History</dt><dd className="mt-1 font-medium">{access.limits.historySessions === null ? "Complete" : `${access.limits.historySessions} sessions`}</dd></div>
              <div><dt className="text-xs text-muted-foreground">Watchlists</dt><dd className="mt-1 font-medium">{access.limits.watchlists}</dd></div>
            </dl>
          </CardContent>
          <CardFooter className="justify-end gap-2">
            <Button nativeButton={false} render={<Link href="/scanners/minervini-vcp/strict" />} variant="outline">Open Strict</Button>
            <Button nativeButton={false} render={<Link href="/scanners/minervini-vcp/custom" />}>Build custom scan</Button>
          </CardFooter>
        </Card>
      </main>
    </div>
  )
}
