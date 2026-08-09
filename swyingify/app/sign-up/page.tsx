import type { Metadata } from "next"

import { AuthPage } from "@/components/auth/auth-page"
import { buildPageMetadata } from "@/lib/seo/metadata"

export const metadata: Metadata = buildPageMetadata({
  title: "Create account",
  description: "Create a Swyingify account for watchlists and stricter scans. Today's shortlist stays free either way.",
  path: "/sign-up",
  noIndex: true,
  robots: { index: false, follow: false },
})

export default function SignUpPage() {
  return <AuthPage mode="sign-up" />
}
