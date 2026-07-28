import React, { useState } from 'react';
import { BookOpen, Sparkles, Star, Save } from 'lucide-react';

export const JournalView: React.FC = () => {
  const [selectedTrade, setSelectedTrade] = useState<string>('SBIN-2026-07-20');
  const [notes, setNotes] = useState<string>(
    `## Trade Setup: VCP Stage-2 Contraction Breakout\n\n- **Entry Rationale**: Contraction 3 (C3) narrowed down to 1.8% with volume drying up completely.\n- **Market Environment**: Nifty was in a strong uptrend above 50 SMA.\n- **Execution Rating**: 5/5 stars. Followed initial plan exactly.`
  );
  const [rating, setRating] = useState<number>(5);

  const sampleJournalList = [
    {
      id: 'SBIN-2026-07-20',
      symbol: 'NSE:SBIN-EQ',
      date: '2026-07-20',
      pnl: 12450.0,
      rating: 5,
      setup: 'VCP C3 Breakout',
    },
    {
      id: 'TATASTEEL-2026-07-18',
      symbol: 'NSE:TATASTEEL-EQ',
      date: '2026-07-18',
      pnl: -3200.0,
      rating: 3,
      setup: '52W High Contraction',
    },
    {
      id: 'RELIANCE-2026-07-14',
      symbol: 'NSE:RELIANCE-EQ',
      date: '2026-07-14',
      pnl: 18900.0,
      rating: 4,
      setup: 'Stage-2 Base Clear',
    },
  ];

  return (
    <div className="flex h-full bg-[#080a0e] font-mono text-xs select-none">
      {/* Left List Pane (30%) */}
      <div className="w-80 border-r border-[#252932] bg-[#0d1117] flex flex-col shrink-0">
        <div className="h-9 px-3 border-b border-[#252932] flex items-center justify-between font-bold text-[#e6edf3]">
          <div className="flex items-center gap-1.5">
            <BookOpen className="w-4 h-4 text-[#3b82f6]" />
            <span>TRADE JOURNAL</span>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto divide-y divide-[#161b22]">
          {sampleJournalList.map((j) => (
            <div
              key={j.id}
              onClick={() => setSelectedTrade(j.id)}
              className={`p-3 cursor-pointer transition-colors ${
                selectedTrade === j.id ? 'bg-[#1c2128] border-l-2 border-[#3b82f6]' : 'hover:bg-[#161b22]'
              }`}
            >
              <div className="flex justify-between items-center mb-1">
                <span className="font-bold text-[#3b82f6]">{j.symbol}</span>
                <span
                  className={`font-bold ${j.pnl >= 0 ? 'text-[#22c55e]' : 'text-[#ef4444]'}`}
                >
                  {j.pnl >= 0 ? '+' : ''}₹{j.pnl.toFixed(2)}
                </span>
              </div>
              <div className="flex justify-between text-[11px] text-[#8b949e]">
                <span>{j.setup}</span>
                <span>{j.date}</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Right Detail Pane (70%) */}
      <div className="flex-1 bg-[#080a0e] flex flex-col overflow-y-auto p-4 gap-4">
        {/* Header Metadata */}
        <div className="bg-[#0d1117] p-3 rounded border border-[#252932] flex items-center justify-between">
          <div>
            <h3 className="font-bold text-sm text-[#3b82f6]">NSE:SBIN-EQ — Trade Journal & Review</h3>
            <span className="text-[11px] text-[#8b949e]">Closed on 2026-07-20 | Realized P&L: +₹12,450.00 (+2.49R)</span>
          </div>

          <div className="flex items-center gap-1">
            {[1, 2, 3, 4, 5].map((star) => (
              <Star
                key={star}
                onClick={() => setRating(star)}
                className={`w-4 h-4 cursor-pointer ${
                  star <= rating ? 'text-[#f59e0b] fill-[#f59e0b]' : 'text-[#252932]'
                }`}
              />
            ))}
          </div>
        </div>

        {/* AI Coach Insights Banner */}
        <div className="bg-[#1c2128] p-3 rounded border border-[#3b82f6]/40 flex gap-3 items-start">
          <Sparkles className="w-5 h-5 text-[#3b82f6] shrink-0 mt-0.5" />
          <div className="space-y-1 text-xs">
            <span className="font-bold text-[#3b82f6] block">AI COACH PATTERN OBSERVATION</span>
            <p className="text-[#e6edf3] leading-relaxed">
              "Great execution on SBIN! You respected your initial SL price without intervention.
              Historical pattern match: In 85% of your VCP Stage-2 trades where entry occurred after 3 contractions, average profit factor is 2.8R."
            </p>
          </div>
        </div>

        {/* Markdown Notes Editor */}
        <div className="flex-1 bg-[#0d1117] rounded border border-[#252932] p-3 flex flex-col gap-2">
          <div className="flex justify-between items-center pb-2 border-b border-[#252932]">
            <span className="font-bold text-[#e6edf3]">TRADER NOTES & THESIS</span>
            <button className="flex items-center gap-1 px-2.5 py-1 rounded bg-[#3b82f6] hover:bg-[#2563eb] text-white text-xs font-bold transition-all">
              <Save className="w-3.5 h-3.5" />
              <span>SAVE NOTES</span>
            </button>
          </div>

          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            className="w-full flex-1 bg-transparent text-[#e6edf3] font-mono text-xs outline-none resize-none leading-relaxed min-h-[200px]"
            placeholder="Document trade setup rationale, market conditions, and personal execution score..."
          />
        </div>
      </div>
    </div>
  );
};
