import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"

import { apiRequest } from "@/lib/api"

export type TradeSide = "buy" | "sell"
export type EntryOrderType = "market" | "limit"
export type TrailingType = "none" | "step_pct" | "atr"
export type ConfirmationPhrase =
  | "CONFIRM_PAPER_TRADE"
  | "CONFIRM_LIVE_ORDER"

export interface ExecutionStatus {
  execution_mode: "paper" | "live"
  live_order_placement_enabled: boolean
  required_confirmation: ConfirmationPhrase
}

export interface TradeInstructionCreate {
  symbol: string
  screening_result_id: string | null
  side: TradeSide
  quantity: number
  product_type: "CNC"
  entry_order_type: EntryOrderType
  planned_entry_price: number
  entry_limit_price: number | null
  initial_stop_loss: number
  initial_target: number | null
  trailing_rule: {
    type: TrailingType
    value: number | null
  }
  risk_amount: number | null
  notes: string | null
}

export interface TradeInstruction {
  id: string
  instrument_id: string
  screening_result_id: string | null
  symbol: string
  display_symbol: string
  side: TradeSide
  quantity: number
  product_type: "CNC"
  entry_order_type: EntryOrderType
  planned_entry_price: string
  entry_limit_price: string | null
  initial_stop_loss: string
  initial_target: string | null
  trailing_rule: {
    type?: TrailingType
    value?: string | number | null
  }
  risk_amount: string | null
  status: "draft" | "confirmed" | "submitted" | "cancelled" | "rejected"
  manual_confirmed_at: string | null
  submitted_at: string | null
  notes: string | null
  created_at: string
  updated_at: string
}

export interface Position {
  id: string
  trade_instruction_id: string | null
  screening_result_id: string | null
  symbol: string
  display_symbol: string
  state:
    | "pending_entry"
    | "open"
    | "trailing_active"
    | "exit_pending"
    | "closed"
    | "cancelled"
  side: "long" | "short"
  quantity: number
  open_quantity: number
  product_type: "CNC"
  average_entry_price: string | null
  current_stop_loss: string | null
  current_target: string | null
  trailing_rule: {
    type?: TrailingType
    value?: string | number | null
  }
  realized_pnl: string
  opened_at: string | null
  closed_at: string | null
  created_at: string
  updated_at: string
}

export interface OrderIntent {
  id: string
  idempotency_key: string
  trade_instruction_id: string | null
  position_id: string | null
  symbol: string
  display_symbol: string
  intent_type: string
  side: TradeSide
  quantity: number
  product_type: "CNC"
  order_type: EntryOrderType | "stop" | "stop_limit"
  limit_price: string | null
  trigger_price: string | null
  status: string
  execution_mode: "paper" | "live"
  fyers_async_id: string | null
  fyers_order_id: string | null
  exchange_order_id: string | null
  broker_requested_at: string | null
  broker_responded_at: string | null
  requested_by_component: "execution_engine"
  reason: string | null
  created_at: string
  updated_at: string
}

export interface TradeConfirmation {
  instruction: TradeInstruction
  position: Position
  order_intent: OrderIntent
  idempotent_replay: boolean
  broker_call_made: boolean
  submission_outcome:
    | "paper_logged"
    | "submitted"
    | "already_submitted"
    | "already_in_progress"
    | "rejected"
    | "submission_unknown"
  submission_message: string | null
}

export const tradingKeys = {
  all: ["trading"] as const,
  executionStatus: () => [...tradingKeys.all, "execution-status"] as const,
  instructions: () => [...tradingKeys.all, "instructions"] as const,
  positions: (activeOnly: boolean) =>
    [...tradingKeys.all, "positions", { activeOnly }] as const,
  orderIntents: () => [...tradingKeys.all, "order-intents"] as const,
}

export function useExecutionStatus() {
  return useQuery({
    queryKey: tradingKeys.executionStatus(),
    queryFn: () => apiRequest<ExecutionStatus>("/trading/execution-status"),
    staleTime: 30_000,
    retry: false,
  })
}

export function useCreateTradeInstruction() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: TradeInstructionCreate) =>
      apiRequest<TradeInstruction>("/trading/trade-instructions", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: tradingKeys.instructions(),
      })
    },
  })
}

export function useConfirmTradeInstruction() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      instructionId,
      confirmation,
    }: {
      instructionId: string
      confirmation: ConfirmationPhrase
    }) =>
      apiRequest<TradeConfirmation>(
        `/trading/trade-instructions/${instructionId}/confirm`,
        {
          method: "POST",
          body: JSON.stringify({
            confirmation,
          }),
        },
      ),
    onSuccess: () => {
      void Promise.all([
        queryClient.invalidateQueries({
          queryKey: tradingKeys.instructions(),
        }),
        queryClient.invalidateQueries({
          queryKey: tradingKeys.positions(true),
        }),
        queryClient.invalidateQueries({
          queryKey: tradingKeys.orderIntents(),
        }),
      ])
    },
  })
}

export function usePositions(activeOnly = true) {
  return useQuery({
    queryKey: tradingKeys.positions(activeOnly),
    queryFn: () =>
      apiRequest<Position[]>(
        `/trading/positions?active_only=${String(activeOnly)}`,
      ),
    staleTime: 3_000,
    refetchInterval: 4_000,
  })
}

export function useOrderIntents() {
  return useQuery({
    queryKey: tradingKeys.orderIntents(),
    queryFn: () =>
      apiRequest<OrderIntent[]>("/trading/order-intents"),
    staleTime: 3_000,
    refetchInterval: 4_000,
  })
}
