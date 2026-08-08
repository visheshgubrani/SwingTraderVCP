import { InfoIcon } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"

export function PreviewNotice({ compact = false }: { compact?: boolean }) {
  return (
    <Alert className="border-primary/15 bg-primary/[0.045] text-foreground">
      <InfoIcon data-icon="inline-start" />
      <AlertTitle>{compact ? "Preview data" : "You are looking at preview data"}</AlertTitle>
      <AlertDescription>
        {compact
          ? "Fictional EOD examples for the Swyingify interface."
          : "These fictional EOD examples show how Minervini-style results will read when the SaaS scanner API is connected."}
      </AlertDescription>
    </Alert>
  )
}

