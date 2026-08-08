import { GeistMono } from "geist/font/mono"
import { GeistSans } from "geist/font/sans"
import { Suspense } from "react"

import { AuthFooter } from "@/components/auth/auth-footer"
import { AuthFormSection } from "@/components/auth/auth-form-view"
import { AuthNav } from "@/components/auth/auth-nav"
import "@/components/auth/auth.css"
import "@/components/landing/landing.css"

export function AuthPage({ mode }: { mode: "sign-in" | "sign-up" }) {
  return (
    <div
      className={`landing auth-page min-h-screen overflow-x-hidden ${GeistSans.variable} ${GeistMono.variable}`}
      style={
        {
          ["--font-landing-body" as string]: "var(--font-geist-sans)",
          ["--font-landing-mono" as string]: "var(--font-geist-mono)",
        } as React.CSSProperties
      }
    >
      <AuthNav mode={mode} />
      <main id="top">
        <Suspense fallback={<div className="mx-auto max-w-md animate-pulse px-6 py-24"><div className="h-[420px] bg-[var(--landing-surface)]" /></div>}>
          <AuthFormSection mode={mode} />
        </Suspense>
      </main>
      <AuthFooter />
    </div>
  )
}
