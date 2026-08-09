"use client"

import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import { useState } from "react"

import { GoogleIcon } from "@/components/auth/google-icon"
import { Reveal } from "@/components/landing/reveal"
import { authClient } from "@/lib/auth-client"
import { cn } from "@/lib/utils"

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

function safeReturnTo(value: string | null): string {
  return value?.startsWith("/") && !value.startsWith("//") && !value.startsWith("/\\") && !value.includes("\u0000")
    ? value
    : "/scanners/minervini-vcp"
}

const copy = {
  "sign-up": {
    title: "Start with today's shortlist.",
    sub: "An account is for watchlists and the stricter scans. Today's shortlist stays free either way.",
    submit: "Create account",
    pwPlaceholder: "At least 8 characters",
    pwError: "Password must be at least 8 characters.",
    emailError: "Enter a valid email address.",
    togglePre: "Already have an account?",
    toggleLabel: "Sign in",
    toggleHref: (returnTo: string) => `/sign-in?returnTo=${encodeURIComponent(returnTo)}`,
  },
  "sign-in": {
    title: "Welcome back.",
    sub: "Sign in to pick up your watchlists and the stricter scans. Today's shortlist is always free.",
    submit: "Sign in",
    pwPlaceholder: "Your password",
    pwError: "Enter your password.",
    emailError: "Enter your account email.",
    togglePre: "New here?",
    toggleLabel: "Create an account",
    toggleHref: (returnTo: string) => `/sign-up?returnTo=${encodeURIComponent(returnTo)}`,
  },
} as const

export function AuthFormView({ mode }: { mode: "sign-in" | "sign-up" }) {
  const router = useRouter()
  const searchParams = useSearchParams()
  const returnTo = safeReturnTo(searchParams.get("returnTo"))
  const text = copy[mode]

  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [showPassword, setShowPassword] = useState(false)
  const [emailError, setEmailError] = useState(false)
  const [passwordError, setPasswordError] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [googleNote, setGoogleNote] = useState(false)
  const [isPending, setIsPending] = useState(false)

  function validate() {
    const trimmed = email.trim()
    const emailOk = EMAIL_RE.test(trimmed)
    const pwOk = password.length >= 8
    setEmailError(!emailOk)
    setPasswordError(!pwOk)
    if (!emailOk || !pwOk) {
      setFormError("Please fix the highlighted fields above.")
      return false
    }
    setEmail(trimmed)
    setFormError(null)
    return true
  }

  async function submitEmail(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!validate()) return

    setIsPending(true)
    setFormError(null)

    try {
      const result =
        mode === "sign-up"
          ? await authClient.signUp.email({
              name: email.split("@")[0] || "Trader",
              email,
              password,
              callbackURL: returnTo,
            })
          : await authClient.signIn.email({ email, password, callbackURL: returnTo })

      if (result.error) {
        setFormError(result.error.message || "We couldn't complete that request. Check your details and try again.")
        return
      }

      router.push(returnTo)
      router.refresh()
    } catch {
      setFormError("We couldn't reach the authentication service. Please try again.")
    } finally {
      setIsPending(false)
    }
  }

  async function continueWithGoogle() {
    setFormError(null)
    setIsPending(true)
    try {
      const result = await authClient.signIn.social({ provider: "google", callbackURL: returnTo })
      if (result.error) {
        setGoogleNote(true)
        setFormError(result.error.message || "Google sign-in is not available right now.")
      }
    } catch {
      setGoogleNote(true)
      setFormError("Google sign-in is not available right now.")
    } finally {
      setIsPending(false)
    }
  }

  return (
    <div className="auth-card">
      <p className="landing-kicker mb-5">Swyingify account</p>
      <h1 className="auth-display mb-4">{text.title}</h1>
      <p className="mb-8 max-w-[42ch] text-base leading-relaxed text-[var(--landing-fg-2)]">{text.sub}</p>

      <button
        type="button"
        className="landing-btn landing-btn-primary w-full gap-2.5"
        onClick={continueWithGoogle}
        disabled={isPending}
      >
        <GoogleIcon />
        {isPending ? "Connecting to Google…" : "Continue with Google"}
      </button>
      {googleNote ? (
        <p className="mt-3 text-xs leading-relaxed text-[var(--landing-muted)]" role="status">
          Google OAuth requires server configuration. Use email below, or set GOOGLE_CLIENT_ID in your environment.
        </p>
      ) : null}

      <div className="my-6 flex items-center gap-3.5 text-xs text-[var(--landing-muted)]">
        <span className="h-px flex-1 bg-[var(--landing-border)]" />
        or continue with email
        <span className="h-px flex-1 bg-[var(--landing-border)]" />
      </div>

      <form onSubmit={submitEmail} noValidate>
        <div className={cn("auth-field mb-[18px]", emailError && "has-error")}>
          <label htmlFor="email" className="mb-2 block text-sm text-[var(--landing-fg-2)]">
            Email
          </label>
          <div className="relative">
            <input
              id="email"
              name="email"
              type="email"
              autoComplete="email"
              spellCheck={false}
              autoCapitalize="none"
              autoCorrect="off"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => {
                setEmail(e.target.value)
                setEmailError(false)
                setFormError(null)
              }}
              aria-invalid={emailError}
              aria-describedby={emailError ? "email-error" : undefined}
            />
          </div>
          {emailError ? (
            <p id="email-error" className="mt-1.5 text-[13px] text-[color-mix(in_oklab,#dc2626_58%,white)]">
              {text.emailError}
            </p>
          ) : null}
        </div>

        <div className={cn("auth-field mb-[18px]", passwordError && "has-error")}>
          <label htmlFor="password" className="mb-2 block text-sm text-[var(--landing-fg-2)]">
            Password
          </label>
          <div className="relative">
            <input
              id="password"
              name="password"
              type={showPassword ? "text" : "password"}
              autoComplete={mode === "sign-up" ? "new-password" : "current-password"}
              placeholder={text.pwPlaceholder}
              value={password}
              onChange={(e) => {
                setPassword(e.target.value)
                setPasswordError(false)
                setFormError(null)
              }}
              aria-invalid={passwordError}
              aria-describedby={passwordError ? "password-error" : undefined}
            />
            <button
              type="button"
              className="absolute right-1 top-1/2 -translate-y-1/2 px-2.5 py-2.5 font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-wider text-[var(--landing-muted)] transition-colors hover:text-[var(--landing-fg)]"
              aria-pressed={showPassword}
              aria-label={showPassword ? "Hide password" : "Show password"}
              onClick={() => setShowPassword((v) => !v)}
            >
              {showPassword ? "Hide" : "Show"}
            </button>
          </div>
          {passwordError ? (
            <p id="password-error" className="mt-1.5 text-[13px] text-[color-mix(in_oklab,#dc2626_58%,white)]">
              {text.pwError}
            </p>
          ) : null}
        </div>

        {formError ? (
          <p className="mb-4 text-[13px] leading-relaxed text-[color-mix(in_oklab,#dc2626_58%,white)]" role="alert">
            {formError}
          </p>
        ) : null}

        <button
          type="submit"
          className="landing-btn auth-submit-ghost min-h-12 w-full disabled:opacity-55"
          disabled={isPending}
        >
          {isPending ? "Please wait…" : text.submit}
        </button>
      </form>

      <p className="mt-[18px] text-xs leading-relaxed text-[var(--landing-muted)]">
        By continuing you agree to the{" "}
        <a href="#" className="text-[var(--landing-fg-2)] underline underline-offset-4">
          Terms of Use
        </a>{" "}
        and acknowledge Swyingify is educational software, not investment advice.
      </p>

      <p className="mt-6 border-t border-[var(--landing-border-soft)] pt-5 text-center text-sm text-[var(--landing-muted)]">
        {text.togglePre}{" "}
        <Link
          href={text.toggleHref(returnTo)}
          className="ml-1.5 inline-block border-b border-[var(--landing-meta)] px-0 py-2 font-[family-name:var(--font-landing-mono)] text-xs uppercase tracking-wider text-[var(--landing-fg)] transition-colors hover:border-[var(--landing-muted)] hover:text-[var(--landing-muted)]"
        >
          {text.toggleLabel}
        </Link>
      </p>
    </div>
  )
}

export function AuthFormSection({ mode }: { mode: "sign-in" | "sign-up" }) {
  return (
    <section className="px-6 py-14 min-[480px]:py-[clamp(56px,8vw,88px)] min-[480px]:pb-[clamp(64px,9vw,96px)] max-sm:px-3">
      <div className="mx-auto max-w-[1200px]">
        <Reveal>
          <AuthFormView mode={mode} />
        </Reveal>
        <Reveal>
          <div className="mt-7 flex flex-col items-center gap-2 text-center">
            <span className="landing-kicker">No card required · The scan is free</span>
            <span className="landing-kicker">Educational only · No orders · No broker</span>
          </div>
        </Reveal>
      </div>
    </section>
  )
}
