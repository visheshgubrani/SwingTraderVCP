import { StatusChip, type StatusTone } from "@/components/terminal/bits"
import { fmtNum, fmtPct, toneCls } from "@/lib/format"
import { cn } from "@/lib/utils"

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
  onOpenSymbol?: (symbol: string) => void;
}

const POSITION_TONES: Record<PositionItem["state"], StatusTone> = {
  open: "work",
  trailing_active: "fill",
  exit_pending: "wait",
  pending_entry: "wait",
  closed: "off",
  cancelled: "off",
}

const POSITION_LABELS: Record<PositionItem["state"], string> = {
  open: "OPEN",
  trailing_active: "TRAILING",
  exit_pending: "EXITING",
  pending_entry: "PENDING",
  closed: "CLOSED",
  cancelled: "CANCELLED",
}

/** Open positions — holdings at last price with software stops and targets. */
export const PositionsTable: React.FC<PositionsTableProps> = ({
  positions,
  onManualExit,
  onOpenSymbol,
}) => {
  const open = positions.filter((p) => !["closed", "cancelled"].includes(p.state))
  const netUnrealized = open.reduce((sum, p) => sum + (p.unrealized_pnl ?? 0), 0)
  const invested = open.reduce(
    (sum, p) => sum + (p.average_entry_price ?? 0) * Math.abs(p.open_quantity),
    0,
  )
  const pct = invested > 0 ? (netUnrealized / invested) * 100 : 0
  const netTone = toneCls(netUnrealized)

  return (
    <section className="view h-full">
      <div className="vhead">
        <div>
          <h2>
            Open Positions <span className="sub">{open.length} lines</span>
          </h2>
          <p className="vmeta">Holdings at last price · software stop / target / trailing levels</p>
        </div>
        <div className="vhead-right">
          <div className="netp">
            <span className="lbl">NET UNREALIZED</span>
            <span className={cn("val", netTone)}>
              {netUnrealized >= 0 ? "+" : ""}₹{fmtNum(netUnrealized)} ({fmtPct(pct)})
            </span>
          </div>
        </div>
      </div>
      <div className="tscroll">
        <table className="tbl">
          <thead>
            <tr>
              <th className="l" style={{ minWidth: 118 }}>SYMBOL</th>
              <th className="l" style={{ minWidth: 64 }}>SIDE</th>
              <th className="l" style={{ minWidth: 84 }}>STATE</th>
              <th style={{ minWidth: 74 }}>QTY</th>
              <th style={{ minWidth: 92 }}>AVG PRICE</th>
              <th style={{ minWidth: 92 }}>STOP</th>
              <th style={{ minWidth: 92 }}>TARGET</th>
              <th style={{ minWidth: 92 }}>LTP</th>
              <th style={{ minWidth: 96 }}>P&L ₹</th>
              <th style={{ minWidth: 80 }}>P&L %</th>
              <th className="l" style={{ minWidth: 110 }}>TRAILING RULE</th>
              <th className="l" style={{ minWidth: 92 }}>ACTIONS</th>
            </tr>
          </thead>
          <tbody>
            {open.length === 0 && (
              <tr>
                <td colSpan={12} className="l" style={{ padding: 26, textAlign: "center" }}>
                  No open positions. Tracked holdings from approved trades appear here.
                </td>
              </tr>
            )}
            {positions.map((pos) => {
              const isClosed = ["closed", "cancelled"].includes(pos.state)
              const long = pos.side === "long"
              const pnl = pos.unrealized_pnl ?? 0
              const pnlTone = toneCls(pnl)
              const invest = (pos.average_entry_price ?? 0) * Math.abs(pos.open_quantity)
              const pnlPct = invest > 0 ? (pnl / invest) * 100 : 0
              const exitable = onManualExit && !isClosed && pos.state !== "pending_entry"
              return (
                <tr className={cn(isClosed && "opacity-45")} key={pos.id}>
                  <td className="l">
                    {onOpenSymbol ? (
                      <button
                        className="symlink"
                        onClick={() => onOpenSymbol(pos.symbol)}
                        title={pos.symbol}
                        type="button"
                      >
                        {pos.symbol}
                      </button>
                    ) : (
                      pos.symbol
                    )}
                  </td>
                  <td className={cn("l", long ? "up" : "down")} style={{ fontWeight: 700 }}>
                    {long ? "LONG" : "SHORT"}
                  </td>
                  <td className="l">
                    <StatusChip tone={POSITION_TONES[pos.state]}>{POSITION_LABELS[pos.state]}</StatusChip>
                  </td>
                  <td>
                    {fmtNum(pos.open_quantity, 0)} / {fmtNum(pos.quantity, 0)}
                  </td>
                  <td>{fmtNum(pos.average_entry_price)}</td>
                  <td className="down">{pos.current_stop_loss !== null ? fmtNum(pos.current_stop_loss) : "—"}</td>
                  <td className="up">{pos.current_target !== null ? fmtNum(pos.current_target) : "—"}</td>
                  <td>{fmtNum(pos.current_ltp)}</td>
                  <td className={pnlTone}>
                    {pos.unrealized_pnl === null
                      ? "—"
                      : `${pnl >= 0 ? "+" : "-"}₹${fmtNum(Math.abs(pnl))}`}
                  </td>
                  <td className={pnlTone}>{fmtPct(pnlPct)}</td>
                  <td className="l" style={{ color: "var(--fg-2)", fontFamily: "var(--font-sans)", fontSize: 11.5 }}>
                    {pos.trailing_rule_desc}
                  </td>
                  <td className="l">
                    {exitable ? (
                      <span className="act">
                        <button className="link-act danger" onClick={() => onManualExit(pos.id)} type="button">
                          Close
                        </button>
                      </span>
                    ) : (
                      <span style={{ color: "var(--muted-text)", fontSize: 10.5 }}>—</span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}
