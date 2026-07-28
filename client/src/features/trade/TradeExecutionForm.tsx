import { useEffect, useRef, useState, type FormEvent } from "react"
import {
  AlertCircleIcon,
  ArrowUpRightIcon,
  CheckCircle2Icon,
  ShieldCheckIcon,
} from "lucide-react"

import {
  Alert,
  AlertDescription,
  AlertTitle,
} from "@/components/ui/alert"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogMedia,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Field,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"
import {
  ToggleGroup,
  ToggleGroupItem,
} from "@/components/ui/toggle-group"
import { useKillSwitch } from "@/features/admin/api"
import {
  useConfirmTradeInstruction,
  useCreateTradeInstruction,
  type EntryOrderType,
  type TradeConfirmation,
  type TradeInstruction,
  type TradeSide,
  type TrailingType,
  useExecutionStatus,
} from "@/features/trade/api"

interface TradeExecutionFormProps {
  symbol: string
  currentLtp: number
  screeningResultId?: string | null
  onTradeConfirmed?: (confirmation: TradeConfirmation) => void
  onValuesChange?: (entry: number, sl: number, target: number) => void
}

function optionalPositiveNumber(value: string): number | null {
  if (value.trim() === "") return null
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
}

export function TradeExecutionForm({
  symbol,
  currentLtp,
  screeningResultId = null,
  onTradeConfirmed,
  onValuesChange,
}: TradeExecutionFormProps) {
  const [side, setSide] = useState<TradeSide>("buy")
  const [orderType, setOrderType] = useState<EntryOrderType>("limit")
  const [quantity, setQuantity] = useState("1")
  const [plannedEntry, setPlannedEntry] = useState(currentLtp.toFixed(2))
  const [stopLoss, setStopLoss] = useState("")
  const [target, setTarget] = useState("")
  const [riskAmount, setRiskAmount] = useState("")
  const [trailingType, setTrailingType] = useState<TrailingType>("none")
  const [trailingValue, setTrailingValue] = useState("")
  const [notes, setNotes] = useState("")
  const [reviewedDraft, setReviewedDraft] =
    useState<TradeInstruction | null>(null)
  const [reviewOpen, setReviewOpen] = useState(false)
  const [lastConfirmation, setLastConfirmation] =
    useState<TradeConfirmation | null>(null)
  const previousSelection = useRef(`${symbol}:${screeningResultId ?? "manual"}`)

  const createInstruction = useCreateTradeInstruction()
  const confirmInstruction = useConfirmTradeInstruction()
  const executionStatus = useExecutionStatus()
  const killSwitch = useKillSwitch()
  const isLive = executionStatus.data?.execution_mode === "live"
  const liveOrdersArmed =
    !isLive || executionStatus.data?.live_order_placement_enabled === true

  useEffect(() => {
    const selection = `${symbol}:${screeningResultId ?? "manual"}`
    if (previousSelection.current === selection) return
    previousSelection.current = selection
    setPlannedEntry(currentLtp.toFixed(2))
    setStopLoss("")
    setTarget("")
    setReviewedDraft(null)
    setLastConfirmation(null)
  }, [currentLtp, screeningResultId, symbol])

  useEffect(() => {
    if (!onValuesChange) return
    onValuesChange(
      optionalPositiveNumber(plannedEntry) ?? 0,
      optionalPositiveNumber(stopLoss) ?? 0,
      optionalPositiveNumber(target) ?? 0,
    )
  }, [onValuesChange, plannedEntry, stopLoss, target])

  const quantityNumber = Number(quantity)
  const entryNumber = optionalPositiveNumber(plannedEntry)
  const stopNumber = optionalPositiveNumber(stopLoss)
  const targetNumber = optionalPositiveNumber(target)
  const riskNumber = optionalPositiveNumber(riskAmount)
  const trailingNumber = optionalPositiveNumber(trailingValue)
  const formError =
    isLive && side === "sell"
      ? "P4 live CNC entry supports buy orders only."
      : !Number.isInteger(quantityNumber) || quantityNumber <= 0
      ? "Enter a positive whole-number quantity."
      : entryNumber === null
        ? "Enter the human-planned entry reference."
        : stopNumber === null
          ? "Enter the stop loss."
          : target.trim() !== "" && targetNumber === null
            ? "Target must be a positive number or left blank."
            : riskAmount.trim() !== "" && riskNumber === null
              ? "Risk amount must be a positive number or left blank."
              : trailingType !== "none" && trailingNumber === null
                ? "Enter a positive trailing value."
                : null

  const requestError =
    (createInstruction.error instanceof Error &&
      createInstruction.error.message) ||
    (confirmInstruction.error instanceof Error &&
      confirmInstruction.error.message) ||
    null

  async function handleReview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (formError || entryNumber === null || stopNumber === null) return

    try {
      const draft = await createInstruction.mutateAsync({
        symbol,
        screening_result_id: screeningResultId,
        side,
        quantity: quantityNumber,
        product_type: "CNC",
        entry_order_type: orderType,
        planned_entry_price: entryNumber,
        entry_limit_price: orderType === "limit" ? entryNumber : null,
        initial_stop_loss: stopNumber,
        initial_target: targetNumber,
        trailing_rule: {
          type: trailingType,
          value: trailingType === "none" ? null : trailingNumber,
        },
        risk_amount: riskNumber,
        notes: notes.trim() || null,
      })
      setReviewedDraft(draft)
      setReviewOpen(true)
    } catch {
      // The mutation exposes the API error in the form.
    }
  }

  async function handleConfirm() {
    if (
      !reviewedDraft ||
      killSwitch.data?.enabled ||
      !executionStatus.data ||
      !liveOrdersArmed
    ) {
      return
    }
    try {
      const result = await confirmInstruction.mutateAsync({
        instructionId: reviewedDraft.id,
        confirmation: executionStatus.data.required_confirmation,
      })
      setLastConfirmation(result)
      setReviewOpen(false)
      onTradeConfirmed?.(result)
    } catch {
      // Keep the review dialog open and show the mutation error.
    }
  }

  return (
    <aside className="flex h-full w-80 shrink-0 flex-col overflow-y-auto border-l border-border bg-card p-3 text-card-foreground">
      <form
        className="flex min-h-full flex-col justify-between gap-4"
        onSubmit={handleReview}
      >
        <div className="flex flex-col gap-4">
          <div className="flex items-center justify-between border-b border-border pb-3">
            <div className="flex items-center gap-2">
              <ShieldCheckIcon aria-hidden="true" />
              <span className="text-sm font-semibold">Trade checkpoint</span>
            </div>
            <Badge variant={isLive ? "destructive" : "outline"}>
              {isLive ? "LIVE" : "PAPER"} · CNC
            </Badge>
          </div>

          <div className="flex items-center justify-between rounded-lg bg-muted p-2.5">
            <div className="flex flex-col gap-0.5">
              <span className="text-xs text-muted-foreground">Target symbol</span>
              <span className="text-sm font-semibold">{symbol}</span>
            </div>
            <span className="text-sm">LTP ₹{currentLtp.toFixed(2)}</span>
          </div>

          {killSwitch.data?.enabled && (
            <Alert variant="destructive">
              <AlertCircleIcon aria-hidden="true" />
              <AlertTitle>Automation paused</AlertTitle>
              <AlertDescription>
                Drafts can still be saved, but confirmation is blocked while
                the global kill switch is engaged.
              </AlertDescription>
            </Alert>
          )}

          {isLive && !liveOrdersArmed && (
            <Alert variant="destructive">
              <AlertCircleIcon aria-hidden="true" />
              <AlertTitle>Live placement is not armed</AlertTitle>
              <AlertDescription>
                Live mode is selected on the backend, but the separate live
                order arming flag is off. Drafts remain available and
                confirmations are blocked.
              </AlertDescription>
            </Alert>
          )}

          {lastConfirmation && (
            <Alert
              variant={
                lastConfirmation.submission_outcome === "rejected" ||
                lastConfirmation.submission_outcome === "submission_unknown"
                  ? "destructive"
                  : "default"
              }
            >
              <CheckCircle2Icon aria-hidden="true" />
              <AlertTitle>
                {lastConfirmation.submission_outcome === "paper_logged"
                  ? "Paper intent logged"
                  : lastConfirmation.submission_outcome === "submitted"
                    ? "Live order submitted"
                    : "Order status requires attention"}
              </AlertTitle>
              <AlertDescription>
                {lastConfirmation.submission_message ??
                  `Position ${lastConfirmation.position.id.slice(0, 8)} is pending entry.`}
              </AlertDescription>
            </Alert>
          )}

          <FieldSet>
            <FieldLegend variant="label">Human trade decision</FieldLegend>
            <FieldGroup className="gap-3">
              <Field>
                <FieldLabel id="trade-side-label">Side</FieldLabel>
                <ToggleGroup
                  aria-labelledby="trade-side-label"
                  className="w-full"
                  value={[side]}
                  onValueChange={(values) => {
                    const value = values[0] as TradeSide | undefined
                    if (value) setSide(value)
                  }}
                  variant="outline"
                  spacing={2}
                >
                  <ToggleGroupItem className="flex-1" value="buy">
                    Buy / long
                  </ToggleGroupItem>
                  <ToggleGroupItem
                    className="flex-1"
                    disabled={isLive}
                    value="sell"
                  >
                    Sell / short
                  </ToggleGroupItem>
                </ToggleGroup>
              </Field>

              <div className="grid grid-cols-2 gap-2">
                <Field>
                  <FieldLabel htmlFor="entry-order-type">
                    Order type
                  </FieldLabel>
                  <NativeSelect
                    className="w-full"
                    id="entry-order-type"
                    value={orderType}
                    onChange={(event) =>
                      setOrderType(event.target.value as EntryOrderType)
                    }
                  >
                    <NativeSelectOption value="limit">Limit</NativeSelectOption>
                    <NativeSelectOption value="market">
                      Market
                    </NativeSelectOption>
                  </NativeSelect>
                </Field>
                <Field
                  data-invalid={
                    quantity !== "" &&
                    (!Number.isInteger(quantityNumber) || quantityNumber <= 0)
                  }
                >
                  <FieldLabel htmlFor="trade-quantity">Quantity</FieldLabel>
                  <Input
                    aria-invalid={
                      quantity !== "" &&
                      (!Number.isInteger(quantityNumber) || quantityNumber <= 0)
                    }
                    id="trade-quantity"
                    min="1"
                    step="1"
                    type="number"
                    value={quantity}
                    onChange={(event) => setQuantity(event.target.value)}
                  />
                </Field>
              </div>

              <Field data-invalid={plannedEntry !== "" && entryNumber === null}>
                <FieldLabel htmlFor="planned-entry">
                  Planned entry reference
                </FieldLabel>
                <Input
                  aria-invalid={plannedEntry !== "" && entryNumber === null}
                  id="planned-entry"
                  min="0.01"
                  step="0.05"
                  type="number"
                  value={plannedEntry}
                  onChange={(event) => setPlannedEntry(event.target.value)}
                />
                <FieldDescription>
                  Required even for market orders; the backend validates the
                  instrument tick size.
                </FieldDescription>
              </Field>

              <div className="grid grid-cols-2 gap-2">
                <Field data-invalid={stopLoss !== "" && stopNumber === null}>
                  <FieldLabel htmlFor="initial-stop">Stop loss</FieldLabel>
                  <Input
                    aria-invalid={stopLoss !== "" && stopNumber === null}
                    id="initial-stop"
                    min="0.01"
                    step="0.05"
                    type="number"
                    value={stopLoss}
                    onChange={(event) => setStopLoss(event.target.value)}
                  />
                </Field>
                <Field data-invalid={target !== "" && targetNumber === null}>
                  <FieldLabel htmlFor="initial-target">
                    Target (optional)
                  </FieldLabel>
                  <Input
                    aria-invalid={target !== "" && targetNumber === null}
                    id="initial-target"
                    min="0.01"
                    step="0.05"
                    type="number"
                    value={target}
                    onChange={(event) => setTarget(event.target.value)}
                  />
                </Field>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <Field>
                  <FieldLabel htmlFor="trailing-type">
                    Trailing rule
                  </FieldLabel>
                  <NativeSelect
                    className="w-full"
                    id="trailing-type"
                    value={trailingType}
                    onChange={(event) =>
                      setTrailingType(event.target.value as TrailingType)
                    }
                  >
                    <NativeSelectOption value="none">None</NativeSelectOption>
                    <NativeSelectOption value="step_pct">
                      Step %
                    </NativeSelectOption>
                    <NativeSelectOption value="atr">
                      ATR multiple
                    </NativeSelectOption>
                  </NativeSelect>
                </Field>
                <Field
                  data-disabled={trailingType === "none"}
                  data-invalid={
                    trailingType !== "none" &&
                    trailingValue !== "" &&
                    trailingNumber === null
                  }
                >
                  <FieldLabel htmlFor="trailing-value">Trail value</FieldLabel>
                  <Input
                    aria-invalid={
                      trailingType !== "none" &&
                      trailingValue !== "" &&
                      trailingNumber === null
                    }
                    disabled={trailingType === "none"}
                    id="trailing-value"
                    min="0.01"
                    step="0.1"
                    type="number"
                    value={trailingValue}
                    onChange={(event) => setTrailingValue(event.target.value)}
                  />
                </Field>
              </div>

              <Field data-invalid={riskAmount !== "" && riskNumber === null}>
                <FieldLabel htmlFor="risk-amount">
                  Risk amount (optional)
                </FieldLabel>
                <Input
                  aria-invalid={riskAmount !== "" && riskNumber === null}
                  id="risk-amount"
                  min="0.01"
                  step="100"
                  type="number"
                  value={riskAmount}
                  onChange={(event) => setRiskAmount(event.target.value)}
                />
              </Field>

              <Field>
                <FieldLabel htmlFor="trade-notes">Decision notes</FieldLabel>
                <Textarea
                  id="trade-notes"
                  maxLength={2000}
                  placeholder="Setup, invalidation, or execution context."
                  value={notes}
                  onChange={(event) => setNotes(event.target.value)}
                />
              </Field>
            </FieldGroup>
          </FieldSet>

          {(formError || requestError) && (
            <Field data-invalid>
              <FieldError>{requestError ?? formError}</FieldError>
            </Field>
          )}
        </div>

        <div className="flex flex-col gap-2 border-t border-border pt-3">
          <Button
            disabled={Boolean(formError) || createInstruction.isPending}
            size="lg"
            type="submit"
          >
            {createInstruction.isPending ? (
              <Spinner data-icon="inline-start" />
            ) : (
              <ArrowUpRightIcon data-icon="inline-start" />
            )}
            {createInstruction.isPending
              ? "Saving draft…"
              : "Save draft & review"}
          </Button>
          <p className="text-center text-xs text-muted-foreground">
            A second, explicit confirmation{" "}
            {isLive ? "submits the live CNC order." : "logs the paper intent."}
          </p>
        </div>
      </form>

      <AlertDialog open={reviewOpen} onOpenChange={setReviewOpen}>
        <AlertDialogContent className="max-w-md">
          <AlertDialogHeader>
            <AlertDialogMedia>
              <ShieldCheckIcon aria-hidden="true" />
            </AlertDialogMedia>
            <AlertDialogTitle>
              {isLive
                ? "Place this live CNC order?"
                : "Confirm paper trade instruction?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {isLive
                ? "This is the required human checkpoint. Confirmation creates the durable intent and then sends a real order to Fyers. This can move money."
                : "This is the required human checkpoint. Confirmation creates a pending position and a paper order intent. It does not contact Fyers."}
            </AlertDialogDescription>
          </AlertDialogHeader>

          {reviewedDraft && (
            <dl className="grid grid-cols-2 gap-2 rounded-lg bg-muted p-3 text-sm">
              <dt className="text-muted-foreground">Symbol</dt>
              <dd>{reviewedDraft.symbol}</dd>
              <dt className="text-muted-foreground">Side / quantity</dt>
              <dd>
                {reviewedDraft.side.toUpperCase()} · {reviewedDraft.quantity}
              </dd>
              <dt className="text-muted-foreground">Entry</dt>
              <dd>
                {reviewedDraft.entry_order_type.toUpperCase()} · ₹
                {reviewedDraft.planned_entry_price}
              </dd>
              <dt className="text-muted-foreground">Stop / target</dt>
              <dd>
                ₹{reviewedDraft.initial_stop_loss} /{" "}
                {reviewedDraft.initial_target
                  ? `₹${reviewedDraft.initial_target}`
                  : "None"}
              </dd>
              <dt className="text-muted-foreground">Mode</dt>
              <dd>
                <Badge variant={isLive ? "destructive" : "outline"}>
                  {isLive ? "LIVE" : "PAPER"} · CNC
                </Badge>
              </dd>
            </dl>
          )}

          {isLive && (
            <Alert variant="destructive">
              <AlertCircleIcon aria-hidden="true" />
              <AlertTitle>Live CNC entry only</AlertTitle>
              <AlertDescription>
                This places a real entry order. Software SL, target, and trailing
                exits are enforced by the backend position monitor once filled.
              </AlertDescription>
            </Alert>
          )}

          {killSwitch.data?.enabled && (
            <Alert variant="destructive">
              <AlertCircleIcon aria-hidden="true" />
              <AlertTitle>Confirmation blocked</AlertTitle>
              <AlertDescription>
                Disengage the global kill switch before confirming.
              </AlertDescription>
            </Alert>
          )}

          {isLive && !liveOrdersArmed && (
            <Alert variant="destructive">
              <AlertCircleIcon aria-hidden="true" />
              <AlertTitle>Live confirmation blocked</AlertTitle>
              <AlertDescription>
                Arm LIVE_ORDER_PLACEMENT_ENABLED on the backend before placing
                this order.
              </AlertDescription>
            </Alert>
          )}

          {requestError && (
            <Alert variant="destructive">
              <AlertCircleIcon aria-hidden="true" />
              <AlertTitle>Could not confirm</AlertTitle>
              <AlertDescription>{requestError}</AlertDescription>
            </Alert>
          )}

          <AlertDialogFooter>
            <AlertDialogCancel disabled={confirmInstruction.isPending}>
              Keep as draft
            </AlertDialogCancel>
            <AlertDialogAction
              disabled={
                !reviewedDraft ||
                confirmInstruction.isPending ||
                killSwitch.data?.enabled ||
                !executionStatus.data ||
                !liveOrdersArmed
              }
              variant={isLive ? "destructive" : "default"}
              onClick={() => void handleConfirm()}
            >
              {confirmInstruction.isPending && (
                <Spinner data-icon="inline-start" />
              )}
              {isLive ? "Place live CNC order" : "Confirm paper trade"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </aside>
  )
}
