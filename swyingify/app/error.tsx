"use client"

import { RefreshCwIcon } from "lucide-react"

import { Button } from "@/components/ui/button"

export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main className="mx-auto flex min-h-[70vh] w-full max-w-xl flex-col items-center justify-center gap-5 px-4 text-center">
      <p className="font-mono text-xs uppercase tracking-[0.2em] text-risk">Something went off-script</p>
      <h1 className="font-display text-4xl font-semibold tracking-tight">The scanner needs a moment.</h1>
      <p className="text-sm leading-6 text-muted-foreground">Refresh this view and try again. Preview data is deterministic, so no research context is lost.</p>
      <Button onClick={reset}><RefreshCwIcon data-icon="inline-start" />Try again</Button>
    </main>
  )
}
