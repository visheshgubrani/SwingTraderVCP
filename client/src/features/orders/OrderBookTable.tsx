import { StatusChip, type StatusTone } from "@/components/terminal/bits"
import { cn } from "@/lib/utils"

export interface OrderIntentItem {
  id: string
  idempotency_key: string
  intent_type: string
  symbol: string
  side: string
  quantity: number
  order_type: string
  limit_price?: number
  status: string
  execution_mode: "paper" | "live"
  fyers_async_id?: string
  fyers_order_id?: string
  reason?: string
  created_at: string
}

interface OrderBookTableProps {
  orders: OrderIntentItem[]
  onOpenSymbol?: (symbol: string) => void
}

function intentTone(status: string): StatusTone {
  const s = status.toLowerCase()
  if (s.includes("filled")) return "fill"
  if (s.includes("rejected")) return "rej"
  if (s.includes("cancelled") || s.includes("cancel") || s.includes("expired")) return "off"
  if (s.includes("partial")) return "wait"
  return "work"
}

function intentLabel(status: string): string {
  const s = status.toLowerCase()
  if (s.includes("partial")) return "PARTIAL"
  if (s.includes("acknowledged")) return "ACK"
  return status.toUpperCase()
}

function fmtTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleTimeString("en-IN", { hour12: false })
}

/** Order intents — engine execution intents (entries, adds, exits). */
export const OrderBookTable: React.FC<OrderBookTableProps> = ({ orders, onOpenSymbol }) => {
  const active = orders.filter((order) => intentTone(order.status) !== "off").length
  const mode = orders[0]?.execution_mode ?? "paper"
  const showMode = orders.some((order) => order.execution_mode !== mode)

  return (
    <section className="view">
      <div className="vhead">
        <div>
          <h2>
            Order Intents <span className="sub">execution-engine intents · entries, adds, exits</span>
          </h2>
          <p className="vmeta">
            <b>{orders.length}</b> intents logged · {active} in flight · {mode.toUpperCase()} account
          </p>
        </div>
        <div className="vhead-right">
          <span className="note-demo">IDEMPOTENT · ENGINE-ISSUED</span>
        </div>
      </div>
      <div className="tscroll">
        <table className="tbl">
          <thead>
            <tr>
              <th className="l" style={{ minWidth: 62 }}>TIME</th>
              <th className="l" style={{ minWidth: 110 }}>SYMBOL</th>
              <th className="l" style={{ minWidth: 84 }}>INTENT</th>
              <th className="l" style={{ minWidth: 52 }}>SIDE</th>
              <th style={{ minWidth: 60 }}>QTY</th>
              <th className="l" style={{ minWidth: 76 }}>TYPE</th>
              <th style={{ minWidth: 92 }}>PRICE</th>
              <th className="l" style={{ minWidth: 88 }}>STATUS</th>
              {showMode && <th className="l" style={{ minWidth: 60 }}>MODE</th>}
              <th style={{ minWidth: 96 }}>BROKER ID</th>
              <th className="l" style={{ minWidth: 150 }}>REASON</th>
            </tr>
          </thead>
          <tbody>
            {orders.length === 0 && (
              <tr>
                <td colSpan={10} className="l" style={{ padding: 26, textAlign: "center" }}>
                  No order intents yet — approved entries, adds and exits appear here.
                </td>
              </tr>
            )}
            {orders.map((order) => {
              const buy = order.side === "buy"
              const tone = intentTone(order.status)
              return (
                <tr key={order.id}>
                  <td className="l">{fmtTime(order.created_at)}</td>
                  <td className="l">
                    {onOpenSymbol ? (
                      <button
                        className="symlink"
                        onClick={() => onOpenSymbol(order.symbol)}
                        title={order.symbol}
                        type="button"
                      >
                        {order.symbol}
                      </button>
                    ) : (
                      order.symbol
                    )}
                  </td>
                  <td className="l" style={{ color: "var(--fg-2)" }}>{order.intent_type}</td>
                  <td className={cn("l", buy ? "up" : "down")} style={{ fontWeight: 700 }}>
                    {order.side.toUpperCase()}
                  </td>
                  <td>{order.quantity}</td>
                  <td className="l">{order.order_type.toUpperCase()}</td>
                  <td>
                    {order.limit_price != null ? `₹${Number(order.limit_price).toFixed(2)}` : "MARKET"}
                  </td>
                  <td className="l">
                    <StatusChip tone={tone}>{intentLabel(order.status)}</StatusChip>
                  </td>
                  {showMode && <td className="l">{order.execution_mode}</td>}
                  <td title={`${order.fyers_order_id ?? order.fyers_async_id ?? ""}`}>
                    {order.fyers_order_id ?? order.fyers_async_id ?? "—"}
                  </td>
                  <td
                    className="l"
                    style={{ color: "var(--fg-2)", fontFamily: "var(--font-sans)", fontSize: 11.5 }}
                    title={order.reason}
                  >
                    {order.reason ?? "—"}
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
