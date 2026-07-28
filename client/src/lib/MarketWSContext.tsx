import { createContext, useContext, type ReactNode } from "react";
import { useMarketWS, type TickData, type TickWorkerStatus } from "./useMarketWS";

interface MarketWSContextValue {
  ltpMap: Map<string, TickData>;
  tickWorkerStatus: TickWorkerStatus | null;
  readyState: number;
  subscribe: (symbols: string[]) => void;
  unsubscribe: (symbols: string[]) => void;
  getLtp: (symbol: string) => TickData | undefined;
}

const MarketWSContext = createContext<MarketWSContextValue | null>(null);

export function MarketWSProvider({ children }: { children: ReactNode }) {
  const ws = useMarketWS();
  return (
    <MarketWSContext.Provider value={ws}>{children}</MarketWSContext.Provider>
  );
}

export function useMarketData() {
  const ctx = useContext(MarketWSContext);
  if (!ctx) throw new Error("useMarketData must be inside MarketWSProvider");
  return ctx;
}

export type { TickData, TickWorkerStatus };
