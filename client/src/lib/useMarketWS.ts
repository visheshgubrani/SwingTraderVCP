import { useCallback, useEffect, useRef, useState } from "react";
import useWebSocketExport from "react-use-websocket";

// Vite 8's dev optimizer exposes this CommonJS package as
// { default: useWebSocket }, while production builds expose the function
// directly. Normalize both shapes before React calls the hook.
const useWebSocket =
  typeof useWebSocketExport === "function"
    ? useWebSocketExport
    : (
        useWebSocketExport as unknown as {
          default: typeof useWebSocketExport;
        }
      ).default;

function resolveWsUrl(): string {
  const explicit = import.meta.env.VITE_WS_URL as string | undefined;
  if (explicit) {
    return explicit;
  }

  const apiBase =
    (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
    "http://localhost:8000/api/v1";

  try {
    const url = new URL(apiBase);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.pathname = "/ws";
    url.search = "";
    url.hash = "";
    return url.toString();
  } catch {
    return "ws://localhost:8000/ws";
  }
}

const WS_URL = resolveWsUrl();

export interface TickData {
  symbol: string;
  ltp: number;
  volume?: number;
  bid?: number;
  ask?: number;
  open?: number;
  high?: number;
  low?: number;
  prev_close?: number;
  change?: number;
  change_pct?: number;
  timestamp?: string;
  received_at?: string;
}

export interface TickWorkerStatus {
  status: string;
  timestamp?: string;
  symbol_count?: number;
}

type LTPMap = Map<string, TickData>;

export function useMarketWS() {
  const [ltpMap, setLtpMap] = useState<LTPMap>(new Map());
  const [tickWorkerStatus, setTickWorkerStatus] = useState<TickWorkerStatus | null>(null);
  const subscribedRef = useRef<Set<string>>(new Set());

  const { sendJsonMessage, lastJsonMessage, readyState } = useWebSocket(WS_URL, {
    shouldReconnect: () => true,
    reconnectInterval: 3000,
    reconnectAttempts: 50,
    onOpen: () => {
      // Re-subscribe on reconnect
      const symbols = Array.from(subscribedRef.current);
      if (symbols.length > 0) {
        sendJsonMessage({ action: "subscribe", symbols });
      }
    },
  });

  // Process incoming messages
  useEffect(() => {
    if (!lastJsonMessage) return;
    const msg = lastJsonMessage as any;

    if (msg.type === "ltp" || (msg.symbol && msg.ltp !== undefined)) {
      // Tick update
      const tick = msg as TickData;
      setLtpMap((prev) => {
        const next = new Map(prev);
        next.set(tick.symbol, tick);
        return next;
      });
    } else if (msg.type === "tick_worker_status") {
      setTickWorkerStatus({
        status: msg.status,
        timestamp: msg.timestamp,
        symbol_count: msg.symbol_count,
      });
    }
  }, [lastJsonMessage]);

  const subscribe = useCallback(
    (symbols: string[]) => {
      const newSymbols = symbols.filter((s) => !subscribedRef.current.has(s));
      if (newSymbols.length === 0) return;

      newSymbols.forEach((s) => subscribedRef.current.add(s));
      sendJsonMessage({ action: "subscribe", symbols: newSymbols });
    },
    [sendJsonMessage],
  );

  const unsubscribe = useCallback(
    (symbols: string[]) => {
      symbols.forEach((s) => subscribedRef.current.delete(s));
      sendJsonMessage({ action: "unsubscribe", symbols });
    },
    [sendJsonMessage],
  );

  const getLtp = useCallback(
    (symbol: string): TickData | undefined => ltpMap.get(symbol),
    [ltpMap],
  );

  return {
    ltpMap,
    tickWorkerStatus,
    readyState,
    subscribe,
    unsubscribe,
    getLtp,
  };
}
