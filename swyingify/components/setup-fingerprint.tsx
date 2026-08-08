import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { QualificationFingerprint } from "@/lib/scanner/types"

const statusClass = {
  strong: "bg-positive",
  supporting: "bg-primary/60",
  watch: "bg-muted-foreground/25",
}

export function SetupFingerprint({
  fingerprint,
  expanded = false,
}: {
  fingerprint: QualificationFingerprint
  expanded?: boolean
}) {
  return (
    <div className={expanded ? "flex flex-col gap-3" : "flex items-center gap-2"}>
      <div className="flex items-center gap-1.5" aria-label={`${fingerprint.strongCount} of ${fingerprint.totalCount} setup checks strong`}>
        <div className="flex items-center gap-1" aria-hidden="true">
          {fingerprint.components.map((item) => (
            <Tooltip key={item.key}>
              <TooltipTrigger
                render={
                  <span
                    className={`h-2 w-5 rounded-full transition-all ${statusClass[item.status]}`}
                  />
                }
              />
              <TooltipContent>
                <span>{item.label}: {item.summary}</span>
              </TooltipContent>
            </Tooltip>
          ))}
        </div>
        <span className="whitespace-nowrap font-mono text-[0.68rem] font-medium text-muted-foreground">
          {fingerprint.strongCount} of {fingerprint.totalCount} strong
        </span>
      </div>
      {expanded && (
        <div className="grid gap-2 sm:grid-cols-2">
          {fingerprint.components.map((item) => (
            <div key={item.key} className="rounded-xl border border-border/70 bg-background/70 p-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-sm font-medium text-foreground">{item.label}</p>
                  <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{item.summary}</p>
                </div>
                <span className="font-mono text-xs text-muted-foreground">{item.score}/{item.maxScore}</span>
              </div>
              <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-muted">
                <div
                  className={`h-full rounded-full ${statusClass[item.status]}`}
                  style={{ width: `${Math.round((item.score / item.maxScore) * 100)}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
