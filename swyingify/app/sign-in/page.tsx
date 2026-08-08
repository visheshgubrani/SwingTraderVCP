import type { Metadata } from "next"

import { AuthPage } from "@/components/auth/auth-page"

export const metadata: Metadata = {
  title: "Sign in",
  description: "Sign in to Swyingify for watchlists and stricter scans. Today's shortlist stays free.",
}

export default function SignInPage() {
  return <AuthPage mode="sign-in" />
}
