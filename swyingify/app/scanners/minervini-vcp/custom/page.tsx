import type { Metadata } from "next"
import { headers } from "next/headers"
import Link from "next/link"
import { LockKeyholeIcon, ShieldCheckIcon } from "lucide-react"

import { AppHeader } from "@/components/app-header"
import { CustomScannerWorkbench } from "@/components/scanners/custom-scanner-workbench"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { hasFeature, resolveAccess } from "@/lib/entitlements"
import { buildPageMetadata } from "@/lib/seo/metadata"

export const metadata: Metadata = buildPageMetadata({
  title: "Custom Minervini scanner",
  description: "Guided Nifty 500 custom scans for Swyingify Pro members.",
  path: "/scanners/minervini-vcp/custom",
  noIndex: true,
})

export const dynamic = "force-dynamic"

export default async function CustomMinerviniPage() {
  const access = await resolveAccess(await headers())
  const allowed = hasFeature(access, "scanner.custom")

  return (
    <div className="min-h-screen bg-background">
      <AppHeader />
      <main className="mx-auto flex w-full max-w-7xl flex-col gap-7 px-4 py-10 sm:px-6 lg:px-8">
        <div className="flex flex-wrap items-end justify-between gap-5">
          <div>
            <p className="font-mono text-xs uppercase tracking-[0.18em] text-muted-foreground">Minervini · custom variant</p>
            <h1 className="mt-3 font-display text-4xl font-semibold tracking-tight">Tune the shortlist, not the engine.</h1>
            <p className="mt-3 max-w-2xl text-muted-foreground">
              Guided controls map to versioned, validated rules. Internal indicator windows and score weights remain fixed so every run stays reproducible.
            </p>
          </div>
          <Button nativeButton={false} render={<Link href="/scanners/minervini-vcp/strict" />} variant="outline">
            Open Strict
          </Button>
        </div>

        {access.isBypassed ? (
          <Alert>
            <ShieldCheckIcon />
            <AlertTitle>{access.tier === "admin" ? "Admin bypass active" : "Development bypass active"}</AlertTitle>
            <AlertDescription>
              {access.tier === "admin"
                ? "Production entitlement checks are bypassed for this admin account."
                : "Production paywalls are disabled in this environment."}
            </AlertDescription>
          </Alert>
        ) : null}

        {allowed ? (
          <CustomScannerWorkbench />
        ) : (
          <Card className="mx-auto w-full max-w-2xl">
            <CardHeader>
              <CardTitle>Custom scans are part of Pro</CardTitle>
              <CardDescription>
                Free accounts can filter tonight&apos;s Standard board. Pro changes the rules and reruns the complete Nifty 500 universe.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Alert>
                <LockKeyholeIcon />
                <AlertTitle>Production entitlement required</AlertTitle>
                <AlertDescription>Includes five custom runs per day and the Minervini Strict board.</AlertDescription>
              </Alert>
            </CardContent>
            <CardFooter className="justify-end gap-2">
              <Button nativeButton={false} render={<Link href="/scanners/minervini-vcp" />} variant="outline">Use Standard free</Button>
              <Button nativeButton={false} render={<Link href="/pricing" />}>See Pro access</Button>
            </CardFooter>
          </Card>
        )}
      </main>
    </div>
  )
}
