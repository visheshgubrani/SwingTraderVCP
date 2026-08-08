import type { Metadata } from "next"

import { AuthPage } from "@/components/auth/auth-page"

export const metadata: Metadata = {
  title: "Create account",
  description: "Create a Swyingify account for watchlists and stricter scans. Today's shortlist stays free either way.",
}

export default function SignUpPage() {
  return <AuthPage mode="sign-up" />
}
