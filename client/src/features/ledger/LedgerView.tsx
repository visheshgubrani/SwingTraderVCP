import React from 'react';

export const LedgerView: React.FC = () => {
  const ledgerEntries = [
    {
      id: 'tx-101',
      date: '2026-07-24',
      ref: 'SETTLE-SBIN-20260720',
      description: 'Realized Settlement Profit: NSE:SBIN-EQ',
      credit: 12450.0,
      debit: 0,
      balance: 1045200.0,
    },
    {
      id: 'tx-100',
      date: '2026-07-24',
      ref: 'TAX-STT-SBIN-20260720',
      description: 'STT Tax & Exchange Turnover Charges',
      credit: 0,
      debit: 142.5,
      balance: 1032750.0,
    },
    {
      id: 'tx-099',
      date: '2026-07-18',
      ref: 'SETTLE-TATASTEEL-20260718',
      description: 'Realized Settlement Loss: NSE:TATASTEEL-EQ',
      credit: 0,
      debit: 3200.0,
      balance: 1032892.5,
    },
    {
      id: 'tx-098',
      date: '2026-07-01',
      ref: 'DEP-INIT-001',
      description: 'Initial Account Deposit',
      credit: 1000000.0,
      debit: 0,
      balance: 1036092.5,
    },
  ];

  return (
    <div className="flex flex-col h-full bg-[#080a0e] font-mono text-xs select-none p-3 gap-3 overflow-y-auto">
      {/* Header Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 shrink-0">
        <div className="bg-[#0d1117] p-3 rounded border border-[#252932]">
          <span className="text-[10px] text-[#8b949e] block">OPENING BALANCE</span>
          <span className="text-lg font-bold text-[#e6edf3]">₹10,00,000.00</span>
        </div>
        <div className="bg-[#0d1117] p-3 rounded border border-[#252932]">
          <span className="text-[10px] text-[#8b949e] block">NET CREDIT (SETTLED)</span>
          <span className="text-lg font-bold text-[#22c55e]">+₹31,350.00</span>
        </div>
        <div className="bg-[#0d1117] p-3 rounded border border-[#252932]">
          <span className="text-[10px] text-[#8b949e] block">TOTAL TAXES & CHARGES</span>
          <span className="text-lg font-bold text-[#ef4444]">-₹342.50</span>
        </div>
        <div className="bg-[#0d1117] p-3 rounded border border-[#252932]">
          <span className="text-[10px] text-[#8b949e] block">CURRENT LEDGER BALANCE</span>
          <span className="text-lg font-bold text-[#3b82f6]">₹10,45,200.00</span>
        </div>
      </div>

      {/* Ledger Table */}
      <div className="flex-1 bg-[#0d1117] rounded border border-[#252932] overflow-hidden flex flex-col">
        <div className="h-9 px-3 border-b border-[#252932] flex items-center justify-between font-bold text-[#e6edf3]">
          <span>RUNNING CAPITAL LEDGER</span>
        </div>

        <div className="flex-1 overflow-auto">
          <table className="w-full text-left border-collapse">
            <thead className="bg-[#080a0e] sticky top-0 border-b border-[#252932] text-[#8b949e] text-[10px] uppercase font-semibold">
              <tr>
                <th className="py-2 px-3">DATE</th>
                <th className="py-2 px-3">REF ID</th>
                <th className="py-2 px-3">DESCRIPTION</th>
                <th className="py-2 px-3 text-right">CREDIT (₹)</th>
                <th className="py-2 px-3 text-right">DEBIT (₹)</th>
                <th className="py-2 px-3 text-right">BALANCE (₹)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#161b22]">
              {ledgerEntries.map((row) => (
                <tr key={row.id} className="hover:bg-[#161b22] transition-colors">
                  <td className="py-2 px-3 text-[#8b949e] text-[11px]">{row.date}</td>
                  <td className="py-2 px-3 font-semibold text-[#3b82f6]">{row.ref}</td>
                  <td className="py-2 px-3 text-[#e6edf3]">{row.description}</td>
                  <td className="py-2 px-3 text-right font-bold text-[#22c55e]">
                    {row.credit > 0 ? `+₹${row.credit.toFixed(2)}` : '-'}
                  </td>
                  <td className="py-2 px-3 text-right font-bold text-[#ef4444]">
                    {row.debit > 0 ? `-₹${row.debit.toFixed(2)}` : '-'}
                  </td>
                  <td className="py-2 px-3 text-right font-bold text-[#e6edf3]">
                    ₹{row.balance.toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
