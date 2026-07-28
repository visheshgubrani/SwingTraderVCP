import React from 'react';

export interface PositionItem {
  id: string;
  symbol: string;
  side: 'long' | 'short';
  quantity: number;
  open_quantity: number;
  average_entry_price: number | null;
  current_ltp: number | null;
  current_stop_loss: number | null;
  current_target: number | null;
  trailing_rule_desc: string;
  realized_pnl: number;
  unrealized_pnl: number | null;
  state: 'pending_entry' | 'open' | 'trailing_active' | 'exit_pending' | 'closed' | 'cancelled';
  opened_at: string | null;
}

interface PositionsTableProps {
  positions: PositionItem[];
  onManualExit?: (positionId: string) => void;
}

export const PositionsTable: React.FC<PositionsTableProps> = ({ positions, onManualExit }) => {
  return (
    <div className="flex flex-col h-full bg-[#080a0e] font-mono text-xs select-none">
      {/* Header Bar */}
      <div className="h-9 bg-[#0d1117] border-b border-[#252932] flex items-center justify-between px-3 shrink-0">
        <div className="flex items-center gap-2">
          <span className="font-bold text-[#e6edf3]">ACTIVE POSITIONS</span>
          <span className="text-[10px] px-1.5 py-0.2 rounded bg-[#161b22] text-[#22c55e] border border-[#252932]">
            {positions.filter((p) => p.state !== 'closed').length} OPEN
          </span>
        </div>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        <table className="w-full text-left border-collapse">
          <thead className="bg-[#0d1117] sticky top-0 border-b border-[#252932] text-[#8b949e] text-[10px] uppercase font-semibold">
            <tr>
              <th className="py-2 px-3">STATE</th>
              <th className="py-2 px-3">SYMBOL</th>
              <th className="py-2 px-3">SIDE</th>
              <th className="py-2 px-3 text-right">QTY</th>
              <th className="py-2 px-3 text-right">AVG ENTRY (₹)</th>
              <th className="py-2 px-3 text-right">LTP (₹)</th>
              <th className="py-2 px-3 text-right">STOP LOSS (₹)</th>
              <th className="py-2 px-3 text-right">TARGET (₹)</th>
              <th className="py-2 px-3 text-[#8b949e]">TRAILING RULE</th>
              <th className="py-2 px-3 text-right">UNREALIZED P&L</th>
              <th className="py-2 px-3 text-center">ACTION</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[#161b22]">
            {positions.map((pos) => {
              const isProfit = (pos.unrealized_pnl ?? 0) >= 0;
              return (
                <tr key={pos.id} className="hover:bg-[#0d1117] transition-colors">
                  <td className="py-2 px-3">
                    {pos.state === 'open' && (
                      <span className="px-1.5 py-0.5 rounded bg-[#22c55e]/10 text-[#22c55e] border border-[#22c55e]/30 text-[10px] font-bold">
                        OPEN
                      </span>
                    )}
                    {pos.state === 'trailing_active' && (
                      <span className="px-1.5 py-0.5 rounded bg-[#3b82f6]/10 text-[#3b82f6] border border-[#3b82f6]/30 text-[10px] font-bold">
                        TRAILING
                      </span>
                    )}
                    {pos.state === 'pending_entry' && (
                      <span className="px-1.5 py-0.5 rounded bg-[#f59e0b]/10 text-[#f59e0b] border border-[#f59e0b]/30 text-[10px] font-bold">
                        PENDING
                      </span>
                    )}
                    {pos.state === 'exit_pending' && (
                      <span className="px-1.5 py-0.5 rounded bg-[#f97316]/10 text-[#f97316] border border-[#f97316]/30 text-[10px] font-bold animate-pulse">
                        EXITING
                      </span>
                    )}
                    {pos.state === 'closed' && (
                      <span className="px-1.5 py-0.5 rounded bg-[#161b22] text-[#8b949e] text-[10px]">
                        CLOSED
                      </span>
                    )}
                    {pos.state === 'cancelled' && (
                      <span className="px-1.5 py-0.5 rounded bg-[#161b22] text-[#8b949e] text-[10px]">
                        CANCELLED
                      </span>
                    )}
                  </td>
                  <td className="py-2 px-3 font-bold text-[#3b82f6]">{pos.symbol}</td>
                  <td className="py-2 px-3 font-semibold uppercase text-[#e6edf3]">
                    {pos.side}
                  </td>
                  <td className="py-2 px-3 text-right font-bold text-[#e6edf3]">
                    {pos.open_quantity} / {pos.quantity}
                  </td>
                  <td className="py-2 px-3 text-right text-[#e6edf3]">
                    {pos.average_entry_price === null
                      ? '—'
                      : `₹${pos.average_entry_price.toFixed(2)}`}
                  </td>
                  <td className="py-2 px-3 text-right font-bold text-[#e6edf3]">
                    {pos.current_ltp === null ? '—' : `₹${pos.current_ltp.toFixed(2)}`}
                  </td>
                  <td className="py-2 px-3 text-right font-bold text-[#ef4444]">
                    {pos.current_stop_loss === null
                      ? '—'
                      : `₹${pos.current_stop_loss.toFixed(2)}`}
                  </td>
                  <td className="py-2 px-3 text-right font-bold text-[#22c55e]">
                    {pos.current_target === null
                      ? '—'
                      : `₹${pos.current_target.toFixed(2)}`}
                  </td>
                  <td className="py-2 px-3 text-[#8b949e] text-[11px]">
                    {pos.trailing_rule_desc}
                  </td>
                  <td
                    className={`py-2 px-3 text-right font-bold ${
                      isProfit ? 'text-[#22c55e]' : 'text-[#ef4444]'
                    }`}
                  >
                    {pos.unrealized_pnl === null
                      ? '—'
                      : `${isProfit ? '+' : ''}₹${pos.unrealized_pnl.toFixed(2)}`}
                  </td>
                  <td className="py-2 px-3 text-center">
                    {onManualExit &&
                      !['closed', 'cancelled', 'pending_entry'].includes(pos.state) && (
                      <button
                        onClick={() => onManualExit(pos.id)}
                        className="px-2 py-0.5 rounded bg-[#ef4444]/10 hover:bg-[#ef4444] text-[#ef4444] hover:text-white border border-[#ef4444]/30 text-[10px] font-bold transition-all"
                      >
                        EXIT
                      </button>
                    )}
                    {!onManualExit && <span className="text-[#8b949e]">—</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};
