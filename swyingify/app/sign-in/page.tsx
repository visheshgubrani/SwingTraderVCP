import type { Metadata } from "next"

import { AuthPage } from "@/components/auth/auth-page"
import { buildPageMetadata } from "@/lib/seo/metadata"

export const metadata: Metadata = buildPageMetadata({
  title: "Sign in",
  description: "Sign in to Swyingify for watchlists and stricter scans. Today's shortlist stays free.",
  path: "/sign-in",
  noIndex: true,
  robots: { index: false, follow: false },
})

export default function SignInPage() {
  return <AuthPage mode="sign-in" />
}
