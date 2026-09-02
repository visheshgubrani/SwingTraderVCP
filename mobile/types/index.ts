export type ExecutionMode = "paper" | "live";

export type ProposalStatus =
  | "proposed"
  | "approved"
  | "rejected"
  | "expired"
  | "cancelled"
  | "executing"
  | "filled";

export type PositionStatus = "open" | "closed" | "partially_closed";

export interface TradeProposal {
  id: string;
  symbol: string;
  pattern_type: string;
  classification: "valid" | "forming" | "not_vcp";
  status: ProposalStatus;
  pivot_price: number;
  stop_loss: number;
  target_1: number;
  target_2?: number;
  target_3?: number;
  risk_per_share: number;
  risk_reward_ratio: number;
  suggested_quantity: number;
  max_risk_budget: number;
  ai_confidence?: number;
  ai_notes?: string;
  chart_url_126?: string;
  chart_url_252?: string;
  created_at: string;
  expires_at: string;
}

export interface Position {
  id: string;
  symbol: string;
  side: "BUY" | "SELL";
  product_type: "CNC" | "INTRADAY";
  entry_price: number;
  current_price: number;
  quantity: number;
  remaining_qty: number;
  unrealized_pnl: number;
  realized_pnl: number;
  pnl_pct: number;
  status: PositionStatus;
  stop_loss_price: number;
  trailing_stage?: string;
  highest_price_seen?: number;
  entry_time: string;
  updated_at: string;
}

export interface ScannerSurvivor {
  symbol: string;
  company_name?: string;
  sector?: string;
  close_price: number;
  change_percent: number;
  volume_ratio: number;
  technical_score: number;
  fundamental_score?: number;
  composite_score: number;
  rs_rating?: number;
  vcp_stage?: string;
  last_scan_at: string;
}

export interface SystemControls {
  kill_switch_active: boolean;
  execution_mode: ExecutionMode;
  broker_authenticated: boolean;
  live_order_placement_enabled: boolean;
  paper_auto_arm_proposals: boolean;
  market_open: boolean;
  last_heartbeat?: string;
}

export interface WatchlistItem {
  symbol: string;
  company_name: string;
  sector: string;
  ltp: number;
  change_pct: number;
  volume: number;
  is_favorite?: boolean;
}
