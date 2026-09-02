import { API_BASE_URL } from "./config";
import type {
  TradeProposal,
  Position,
  ScannerSurvivor,
  SystemControls,
  WatchlistItem,
} from "../types";

class ApiError extends Error {
  constructor(public status: number, message: string, public body?: any) {
    super(message);
    this.name = "ApiError";
  }
}

async function fetchJson<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${API_BASE_URL}${path}`;
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...options?.headers,
    },
    ...options,
  });

  if (!response.ok) {
    let errorDetail = response.statusText;
    let body;
    try {
      body = await response.json();
      if (body?.detail) errorDetail = body.detail;
    } catch {
      // Ignored
    }
    throw new ApiError(response.status, errorDetail, body);
  }

  return response.json() as Promise<T>;
}

export const api = {
  // System Controls & Execution Status
  getExecutionStatus: () => fetchJson<{
    execution_mode: "paper" | "live";
    live_order_placement_enabled: boolean;
    required_confirmation: string;
  }>("/trading/execution-status"),

  getKillSwitch: () => fetchJson<{
    active: boolean;
    reason?: string;
    updated_at?: string;
  }>("/system/kill-switch"),

  updateKillSwitch: (active: boolean, reason?: string) =>
    fetchJson<{ active: boolean; reason?: string }>("/system/kill-switch", {
      method: "PUT",
      body: JSON.stringify({ active, reason: reason ?? "Updated from Mobile App" }),
    }),

  // Positions & Risk
  getPositions: () => fetchJson<Position[]>("/trading/positions"),

  // Trade Proposals / Instructions
  getProposals: () => fetchJson<TradeProposal[]>("/trading/instructions"),

  getProposalById: (id: string) =>
    fetchJson<TradeProposal>(`/trading/instructions/${id}`),

  confirmProposal: (id: string, confirmationText: string) =>
    fetchJson<{ status: string; instruction_id: string }>(
      `/trading/instructions/${id}/confirm`,
      {
        method: "POST",
        body: JSON.stringify({ confirmation: confirmationText }),
      }
    ),

  // Scanner Results
  getLatestScanRun: () =>
    fetchJson<{
      id: string;
      as_of_date: string;
      status: string;
      survivors_count: number;
    }>("/screening/runs/latest"),

  getScanResults: (runId?: string) =>
    fetchJson<{
      results: ScannerSurvivor[];
      total: number;
    }>(runId ? `/screening/results?scan_run_id=${runId}` : "/screening/results"),

  // Watchlist
  getWatchlist: () => fetchJson<WatchlistItem[]>("/trading/watchlist"),
};
