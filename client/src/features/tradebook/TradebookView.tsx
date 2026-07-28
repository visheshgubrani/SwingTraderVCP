import React from 'react';

export interface ClosedTradeItem {
  id: string;
  symbol: string;
  side: 'long' | 'short';
  quantity: number;
  entry_price: number;
  exit_price: number;
  realized_pnl: number;
  r_multiple: number;
  hold_duration_days: number;
  opened_at: string;
  closed_at: string;
  exit_reason: 'STOP_LOSS' | 'TARGET' | 'TRAILING_STOP' | 'MANUAL';
}

interface TradebookViewProps {
  trades: ClosedTradeItem[];
}

export const TradebookView: React.FC<TradebookViewProps> = ({ trades }) => {
  const totalTrades = trades.length;
  const wins = trades.filter((t) => t.realized_pnl > 0).length;
  const winRate = totalTrades > 0 ? (wins / totalTrades) * 100 : 0;
  const totalPnl = trades.reduce((acc, t) => acc + t.realized_pnl, 0);

  return (
    <div className="flex flex-col h-full bg-[#080a0e] font-mono text-xs select-none p-3 gap-3 overflow-y-auto">
      {/* Top Stat Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 shrink-0">
        <div className="bg-[#0d1117] p-3 rounded border border-[#252932]">
          <span className="text-[10px] text-[#8b949e] block">TOTAL CLOSED TRADES</span>
          <span className="text-lg font-bold text-[#e6edf3]">{totalTrades}</span>
        </div>
        <div className="bg-[#0d1117] p-3 rounded border border-[#252932]">
          <span className="text-[10px] text-[#8b949e] block">WIN RATE %</span>
          <span className="text-lg font-bold text-[#3b82f6]">{winRate.toFixed(1)}%</span>
        </div>
        <div className="bg-[#0d1117] p-3 rounded border border-[#252932]">
          <span className="text-[10px] text-[#8b949e] block">NET REALIZED P&L</span>
          <span
            className={`text-lg font-bold ${
              totalPnl >= 0 ? 'text-[#22c55e]' : 'text-[#ef4444]'
            }`}
          >
            {totalPnl >= 0 ? '+' : ''}₹{totalPnl.toFixed(2)}
          </span>
        </div>
        <div className="bg-[#0d1117] p-3 rounded border border-[#252932]">
          <span className="text-[10px] text-[#8b949e] block">AVG R-MULTIPLE</span>
          <span className="text-lg font-bold text-[#22c55e]">2.14 R</span>
        </div>
      </div>

      {/* Trades Table */}
      <div className="flex-1 bg-[#0d1117] rounded border border-[#252932] overflow-hidden flex flex-col">
        <div className="h-9 px-3 border-b border-[#252932] flex items-center justify-between font-bold text-[#e6edf3]">
          <span>CLOSED TRADE HISTORY</span>
        </div>

        <div className="flex-1 overflow-auto">
          <table className="w-full text-left border-collapse">
            <thead className="bg-[#080a0e] sticky top-0 border-b border-[#252932] text-[#8b949e] text-[10px] uppercase font-semibold">
              <tr>
                <th className="py-2 px-3">CLOSED DATE</th>
                <th className="py-2 px-3">SYMBOL</th>
                <th className="py-2 px-3">SIDE</th>
                <th className="py-2 px-3 text-right">QTY</th>
                <th className="py-2 px-3 text-right">ENTRY (₹)</th>
                <th className="py-2 px-3 text-right">EXIT (₹)</th>
                <th className="py-2 px-3 text-right">REALIZED P&L</th>
                <th className="py-2 px-3 text-right">R-MULTIPLE</th>
                <th className="py-2 px-3 text-center">HOLD (DAYS)</th>
                <th className="py-2 px-3 text-center">EXIT REASON</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#161b22]">
              {trades.map((t) => {
                const isWin = t.realized_pnl >= 0;
                return (
                  <tr key={t.id} className="hover:bg-[#161b22] transition-colors">
                    <td className="py-2 px-3 text-[#8b949e] text-[11px]">
                      {new Date(t.closed_at).toLocaleDateString('en-IN')}
                    </td>
                    <td className="py-2 px-3 font-bold text-[#3b82f6]">{t.symbol}</td>
                    <td className="py-2 px-3 font-semibold uppercase text-[#e6edf3]">
                      {t.side}
                    </td>
                    <td className="py-2 px-3 text-right font-bold text-[#e6edf3]">
                      {t.quantity}
                    </td>
                    <td className="py-2 px-3 text-right text-[#e6edf3]">
                      ₹{t.entry_price.toFixed(2)}
                    </td>
                    <td className="py-2 px-3 text-right text-[#e6edf3]">
                      ₹{t.exit_price.toFixed(2)}
                    </td>
                    <td
                      className={`py-2 px-3 text-right font-bold ${
                        isWin ? 'text-[#22c55e]' : 'text-[#ef4444]'
                      }`}
                    >
                      {isWin ? '+' : ''}₹{t.realized_pnl.toFixed(2)}
                    </td>
                    <td
                      className={`py-2 px-3 text-right font-bold ${
                        isWin ? 'text-[#22c55e]' : 'text-[#ef4444]'
                      }`}
                    >
                      {t.r_multiple > 0 ? '+' : ''}
                      {t.r_multiple.toFixed(2)}R
                    </td>
                    <td className="py-2 px-3 text-center text-[#e6edf3]">
                      {t.hold_duration_days}d
                    </td>
                    <td className="py-2 px-3 text-center">
                      <span className="px-1.5 py-0.5 rounded bg-[#161b22] text-[#8b949e] border border-[#252932] text-[10px]">
                        {t.exit_reason}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
