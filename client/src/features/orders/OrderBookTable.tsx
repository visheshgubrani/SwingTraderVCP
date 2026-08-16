import React from 'react';
import { CheckCircle, Clock, XCircle } from 'lucide-react';

export interface OrderIntentItem {
  id: string;
  idempotency_key: string;
  intent_type: string;
  symbol: string;
  side: string;
  quantity: number;
  order_type: string;
  limit_price?: number;
  status: string;
  execution_mode: 'paper' | 'live';
  fyers_async_id?: string;
  fyers_order_id?: string;
  reason?: string;
  created_at: string;
}

interface OrderBookTableProps {
  orders: OrderIntentItem[];
}

export const OrderBookTable: React.FC<OrderBookTableProps> = ({ orders }) => {
  return (
    <div className="flex flex-col h-full bg-[#080a0e] font-mono text-xs select-none">
      {/* Header Bar */}
      <div className="h-9 bg-[#0d1117] border-b border-[#252932] flex items-center justify-between px-3 shrink-0">
        <div className="flex items-center gap-2">
          <span className="font-bold text-[#e6edf3]">TODAY ORDER BOOK</span>
          <span className="text-[10px] px-1.5 py-0.2 rounded bg-[#161b22] text-[#3b82f6] border border-[#252932]">
            {orders.length} INTENTS LOGGED
          </span>
        </div>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        <table className="w-full text-left border-collapse">
          <thead className="bg-[#0d1117] sticky top-0 border-b border-[#252932] text-[#8b949e] text-[10px] uppercase font-semibold">
            <tr>
              <th className="py-2 px-3">TIME</th>
              <th className="py-2 px-3">INTENT TYPE</th>
              <th className="py-2 px-3">SYMBOL</th>
              <th className="py-2 px-3">SIDE</th>
              <th className="py-2 px-3">TYPE</th>
              <th className="py-2 px-3 text-right">QTY</th>
              <th className="py-2 px-3 text-right">LIMIT PRICE</th>
              <th className="py-2 px-3">MODE</th>
              <th className="py-2 px-3">BROKER ID</th>
              <th className="py-2 px-3 text-center">STATUS</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[#161b22]">
            {orders.map((ord) => (
              <tr key={ord.id} className="hover:bg-[#0d1117] transition-colors">
                <td className="py-2 px-3 text-[#8b949e] text-[11px]">
                  {new Date(ord.created_at).toLocaleTimeString('en-IN', { hour12: false })}
                </td>
                <td className="py-2 px-3 font-semibold text-[#e6edf3] uppercase">
                  {ord.intent_type}
                </td>
                <td className="py-2 px-3 font-bold text-[#3b82f6]">{ord.symbol}</td>
                <td className="py-2 px-3 font-semibold uppercase text-[#e6edf3]">
                  {ord.side}
                </td>
                <td className="py-2 px-3 text-[#8b949e] uppercase">{ord.order_type}</td>
                <td className="py-2 px-3 text-right font-bold text-[#e6edf3]">
                  {ord.quantity}
                </td>
                <td className="py-2 px-3 text-right text-[#e6edf3]">
                  {ord.limit_price ? `₹${ord.limit_price.toFixed(2)}` : 'MARKET'}
                </td>
                <td className="py-2 px-3 text-[#8b949e] text-[11px] uppercase">
                  {ord.execution_mode}
                </td>
                <td className="py-2 px-3 text-[#8b949e] text-[11px]">
                  {ord.fyers_order_id || ord.fyers_async_id || '-'}
                </td>
                <td className="py-2 px-3 text-center">
                  {ord.status === 'filled' && (
                    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-[#22c55e]/10 text-[#22c55e] border border-[#22c55e]/30 text-[10px] font-bold">
                      <CheckCircle className="w-3 h-3" /> FILLED
                    </span>
                  )}
                  {ord.execution_mode === 'paper' && ord.status === 'created' && (
                    <span
                      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-[#f59e0b]/10 text-[#f59e0b] border border-[#f59e0b]/30 text-[10px] font-bold"
                      title={ord.reason}
                    >
                      <Clock className="w-3 h-3" /> PENDING
                    </span>
                  )}
                  {(ord.execution_mode === 'live' || ord.execution_mode === 'paper') &&
                    (ord.status === 'submitted' || ord.status === 'acknowledged' || ord.status === 'partially_filled') && (
                    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-[#f59e0b]/10 text-[#f59e0b] border border-[#f59e0b]/30 text-[10px] font-bold">
                      <Clock className="w-3 h-3" /> PENDING
                    </span>
                  )}
                  {(ord.status === 'rejected' || ord.status === 'cancelled') && (
                    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-[#ef4444]/10 text-[#ef4444] border border-[#ef4444]/30 text-[10px] font-bold">
                      <XCircle className="w-3 h-3" /> {ord.status.toUpperCase()}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};
