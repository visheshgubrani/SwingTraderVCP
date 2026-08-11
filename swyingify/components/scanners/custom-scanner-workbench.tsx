"use client"

import { useMutation, useQuery } from "@tanstack/react-query"
import { useState } from "react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field"
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { createScannerVariant, fetchScannerVariant } from "@/lib/scanner/api"
import type { ScannerVariantInput } from "@/lib/scanner/types"
import { InfoIcon } from "lucide-react"

const DEFAULT_INPUT: ScannerVariantInput = {
  minRsRating: 80,
  maxDistance52WeekHighPct: 15,
  minAdtvCrore: 25,
  stage2ChecksRequired: 5,
  contraction: "tight",
  volumeDryUp: "strong",
  minimumTechnicalScore: 80,
}

export function CustomScannerWorkbench() {
  const [input, setInput] = useState<ScannerVariantInput>(DEFAULT_INPUT)
  const [runId, setRunId] = useState<string | null>(null)

  const createRun = useMutation({
    mutationFn: createScannerVariant,
    onSuccess: (run) => setRunId(run.runId),
  })
  const runQuery = useQuery({
    queryKey: ["scanner", "variant", runId],
    queryFn: () => fetchScannerVariant(runId as string),
    enabled: Boolean(runId),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === "queued" || status === "running" ? 3_000 : false
    },
    staleTime: 2_000,
  })

  const run = runQuery.data
  const busy = createRun.isPending || run?.status === "queued" || run?.status === "running"
  const error = createRun.error ?? runQuery.error

  function update<K extends keyof ScannerVariantInput>(
    key: K,
    value: ScannerVariantInput[K],
  ) {
    setInput((current) => ({ ...current, [key]: value }))
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[380px_minmax(0,1fr)]">
      <Card className="h-fit">
        <CardHeader>
          <CardTitle>Define your shortlist</CardTitle>
          <CardDescription>
            These controls change the full Nifty 500 scan. They do not merely filter the public top 25.
          </CardDescription>
          <CardAction>
            <Badge variant="outline">5 runs / day</Badge>
          </CardAction>
        </CardHeader>
        <CardContent>
          <form
            id="custom-scanner-form"
            onSubmit={(event) => {
              event.preventDefault()
              setRunId(null)
              createRun.mutate(input)
            }}
          >
            <FieldSet>
              <FieldLegend>Trend and liquidity</FieldLegend>
              <FieldGroup>
                <Field>
                  <FieldLabel htmlFor="variant-rs">Minimum RS rating</FieldLabel>
                  <NativeSelect
                    id="variant-rs"
                    className="w-full"
                    value={input.minRsRating}
                    onChange={(event) => update("minRsRating", Number(event.target.value) as ScannerVariantInput["minRsRating"])}
                  >
                    {[60, 70, 80, 90].map((value) => <NativeSelectOption key={value} value={value}>{value}</NativeSelectOption>)}
                  </NativeSelect>
                  <FieldDescription>Higher values keep stronger universe leaders.</FieldDescription>
                </Field>
                <Field>
                  <FieldLabel htmlFor="variant-stage2">Stage 2 checks</FieldLabel>
                  <NativeSelect
                    id="variant-stage2"
                    className="w-full"
                    value={input.stage2ChecksRequired}
                    onChange={(event) => update("stage2ChecksRequired", Number(event.target.value) as 4 | 5)}
                  >
                    <NativeSelectOption value={4}>At least 4 of 5</NativeSelectOption>
                    <NativeSelectOption value={5}>All 5 checks</NativeSelectOption>
                  </NativeSelect>
                </Field>
                <Field>
                  <FieldLabel htmlFor="variant-adtv">Minimum ADTV</FieldLabel>
                  <NativeSelect
                    id="variant-adtv"
                    className="w-full"
                    value={input.minAdtvCrore}
                    onChange={(event) => update("minAdtvCrore", Number(event.target.value) as ScannerVariantInput["minAdtvCrore"])}
                  >
                    {[10, 25, 50, 100].map((value) => <NativeSelectOption key={value} value={value}>₹{value} crore</NativeSelectOption>)}
                  </NativeSelect>
                </Field>
              </FieldGroup>
            </FieldSet>

            <FieldSet className="mt-6">
              <FieldLegend>Base quality</FieldLegend>
              <FieldGroup>
                <Field>
                  <FieldLabel htmlFor="variant-high">Maximum distance from 52-week high</FieldLabel>
                  <NativeSelect
                    id="variant-high"
                    className="w-full"
                    value={input.maxDistance52WeekHighPct}
                    onChange={(event) => update("maxDistance52WeekHighPct", Number(event.target.value) as ScannerVariantInput["maxDistance52WeekHighPct"])}
                  >
                    {[5, 10, 15, 25].map((value) => <NativeSelectOption key={value} value={value}>{value}%</NativeSelectOption>)}
                  </NativeSelect>
                </Field>
                <Field>
                  <FieldLabel htmlFor="variant-contraction">Contraction quality</FieldLabel>
                  <NativeSelect
                    id="variant-contraction"
                    className="w-full"
                    value={input.contraction}
                    onChange={(event) => update("contraction", event.target.value as ScannerVariantInput["contraction"])}
                  >
                    <NativeSelectOption value="balanced">Balanced</NativeSelectOption>
                    <NativeSelectOption value="tight">Tight</NativeSelectOption>
                    <NativeSelectOption value="very_tight">Very tight</NativeSelectOption>
                  </NativeSelect>
                </Field>
                <Field>
                  <FieldLabel htmlFor="variant-volume">Volume dry-up</FieldLabel>
                  <NativeSelect
                    id="variant-volume"
                    className="w-full"
                    value={input.volumeDryUp}
                    onChange={(event) => update("volumeDryUp", event.target.value as ScannerVariantInput["volumeDryUp"])}
                  >
                    <NativeSelectOption value="normal">Normal</NativeSelectOption>
                    <NativeSelectOption value="strong">Strong</NativeSelectOption>
                    <NativeSelectOption value="extreme">Extreme</NativeSelectOption>
                  </NativeSelect>
                </Field>
                <Field>
                  <FieldLabel htmlFor="variant-score">Minimum technical score</FieldLabel>
                  <NativeSelect
                    id="variant-score"
                    className="w-full"
                    value={input.minimumTechnicalScore}
                    onChange={(event) => update("minimumTechnicalScore", Number(event.target.value) as 70 | 80 | 90)}
                  >
                    {[70, 80, 90].map((value) => <NativeSelectOption key={value} value={value}>{value}</NativeSelectOption>)}
                  </NativeSelect>
                </Field>
              </FieldGroup>
            </FieldSet>
          </form>
        </CardContent>
        <CardFooter className="justify-between gap-3">
          <span className="text-xs text-muted-foreground">
            {run ? `${run.quotaRemaining} runs left today` : "Latest EOD data"}
          </span>
          <Button type="submit" form="custom-scanner-form" disabled={busy}>
            {busy ? "Scanning Nifty 500…" : "Run custom scan"}
          </Button>
        </CardFooter>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Custom results</CardTitle>
          <CardDescription>
            {run?.asOfDate ? `End-of-day snapshot for ${run.asOfDate}.` : "Your result set will appear here when the worker finishes."}
          </CardDescription>
          {run ? <CardAction><Badge variant="secondary">{run.status}</Badge></CardAction> : null}
        </CardHeader>
        <CardContent>
          {error ? (
            <Alert variant="destructive">
              <InfoIcon />
              <AlertTitle>Custom scan unavailable</AlertTitle>
              <AlertDescription>{error.message}</AlertDescription>
            </Alert>
          ) : run?.status === "succeeded" ? (
            run.results.length > 0 ? (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Stock</TableHead>
                    <TableHead>Sector</TableHead>
                    <TableHead className="text-right">Score</TableHead>
                    <TableHead className="text-right">RS</TableHead>
                    <TableHead className="text-right">From high</TableHead>
                    <TableHead className="text-right">ADTV</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {run.results.map((result) => (
                    <TableRow key={result.id}>
                      <TableCell>
                        <span className="font-mono font-medium">{result.symbol}</span>
                      </TableCell>
                      <TableCell className="max-w-52 truncate">{result.sector}</TableCell>
                      <TableCell className="text-right font-mono">{result.technicalScore.toFixed(1)}</TableCell>
                      <TableCell className="text-right font-mono">{result.rsRating}</TableCell>
                      <TableCell className="text-right font-mono">{result.pctFrom52WeekHigh.toFixed(1)}%</TableCell>
                      <TableCell className="text-right font-mono">₹{result.adtvCrore.toFixed(0)}cr</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              <Alert>
                <InfoIcon />
                <AlertTitle>No stocks passed every gate</AlertTitle>
                <AlertDescription>Broaden one control and run the scan again.</AlertDescription>
              </Alert>
            )
          ) : (
            <div className="flex min-h-80 items-center justify-center text-center text-sm text-muted-foreground">
              {busy ? "The scan worker is evaluating the Nifty 500…" : "Choose your controls and run the scanner."}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
