import Link from "next/link"

import { cn } from "@/lib/utils"

function BrandIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      focusable="false"
      className={className}
    >
      <rect x="0.5" y="0.5" width="15" height="15" stroke="currentColor" strokeWidth="1" opacity="0.9" />
      <path
        d="M3 9.2 L5 9.2 L5 7.4 L7.2 7.4 L7.2 5.8 L9.6 5.8 L9.6 3.8 L11.2 3.8"
        stroke="currentColor"
        strokeWidth="1.05"
        strokeLinecap="square"
        strokeLinejoin="miter"
      />
      <path
        d="M11.2 3.8 L13 3.8 L13 5.6"
        stroke="currentColor"
        strokeWidth="1.05"
        strokeLinecap="square"
        strokeLinejoin="miter"
      />
    </svg>
  )
}

type LandingBrandProps = {
  href?: string
  compact?: boolean
  className?: string
}

export function LandingBrand({ href = "/", compact = false, className }: LandingBrandProps) {
  return (
    <Link
      href={href}
      className={cn("landing-brand-lockup", compact && "landing-brand-lockup-compact", className)}
      aria-label="Swyingify — home"
    >
      <span className="landing-brand-mark" aria-hidden="true">
        <BrandIcon />
      </span>
      <span className="landing-brand-text">
        <span className="landing-brand-word">Swyingify</span>
        <span className="landing-brand-meta">Nifty 500 · VCP</span>
      </span>
    </Link>
  )
}
