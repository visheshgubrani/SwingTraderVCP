import Link from "next/link"

import { BrandMark } from "@/components/brand-mark"
import { Button } from "@/components/ui/button"

export default function NotFound() {
  return (
    <main className="mx-auto flex min-h-[70vh] w-full max-w-xl flex-col items-center justify-center gap-5 px-4 text-center">
      <BrandMark />
      <p className="font-mono text-xs uppercase tracking-[0.2em] text-muted-foreground">No preview found</p>
      <h1 className="font-display text-4xl font-semibold tracking-tight">That symbol is outside today’s board.</h1>
      <p className="text-sm leading-6 text-muted-foreground">Try one of the fictional preview companies from the Minervini scanner.</p>
      <Button nativeButton={false} render={<Link href="/scanner" />}>Back to scanner</Button>
    </main>
  )
}
