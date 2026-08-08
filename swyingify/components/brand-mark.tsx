import Link from "next/link"

export function BrandMark({ compact = false }: { compact?: boolean }) {
  return (
    <Link href="/" className="group inline-flex items-center gap-2.5" aria-label="Swyingify home">
      <span className="relative grid size-9 place-items-center overflow-hidden rounded-xl bg-primary text-primary-foreground shadow-sm transition-transform duration-300 group-hover:-rotate-3">
        <span className="absolute bottom-2 left-2 h-1.5 w-1.5 rounded-full bg-primary-foreground/80" />
        <span className="absolute bottom-2 left-4 h-3 w-1.5 rounded-full bg-primary-foreground/90" />
        <span className="absolute bottom-2 left-6 h-5 w-1.5 rounded-full bg-primary-foreground" />
        <span className="absolute bottom-2 left-2 right-2 h-px bg-primary-foreground/40" />
      </span>
      {!compact && <span className="font-display text-lg font-semibold tracking-tight text-foreground">Swyingify</span>}
    </Link>
  )
}

