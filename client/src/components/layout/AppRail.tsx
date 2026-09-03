import { NavLink } from "react-router"
import {
  BookCheckIcon,
  BookOpenIcon,
  BriefcaseBusinessIcon,
  CandlestickChartIcon,
  FlaskConicalIcon,
  LandmarkIcon,
  ListOrderedIcon,
  ScanSearchIcon,
  SlidersHorizontalIcon,
  SparklesIcon,
  type LucideIcon,
} from "lucide-react"

import { useMarketData } from "@/lib/MarketWSContext"
import { cn } from "@/lib/utils"

interface RailItem {
  to: string
  label: string
  icon: LucideIcon
  end?: boolean
}

const CORE: RailItem[] = [
  { to: "/", label: "Chart — chart view", icon: CandlestickChartIcon, end: true },
  { to: "/scanner", label: "Scanner — VCP scoreboard", icon: ScanSearchIcon },
  { to: "/positions", label: "Open positions", icon: BriefcaseBusinessIcon },
  { to: "/orders", label: "Order intents", icon: ListOrderedIcon },
  { to: "/tradebook", label: "Tradebook", icon: BookCheckIcon },
]

const EXTRA: RailItem[] = [
  { to: "/proposals", label: "Trade proposals", icon: SparklesIcon },
  { to: "/fundamentals", label: "Fundamentals", icon: FlaskConicalIcon },
  { to: "/journal", label: "Journal", icon: BookOpenIcon },
  { to: "/operations", label: "Operations", icon: SlidersHorizontalIcon },
  { to: "/ledger", label: "Paper ledger", icon: LandmarkIcon },
]

function RailLink({ item }: { item: RailItem }) {
  const Icon = item.icon
  return (
    <NavLink
      aria-label={item.label}
      className={({ isActive }) => cn("rib", isActive && "on")}
      end={item.end}
      title={item.label}
      to={item.to}
    >
      <Icon aria-hidden="true" className="rib-ic" strokeWidth={1.6} />
    </NavLink>
  )
}

/** Left icon rail — module navigation (design .rail). */
export function AppRail() {
  const { tickWorkerStatus } = useMarketData()
  const running = (tickWorkerStatus?.status ?? "").toLowerCase().includes("run")

  return (
    <aside className="rail max-[940px]:hidden">
      {CORE.map((item) => (
        <RailLink item={item} key={item.to} />
      ))}
      <span className="rsep" aria-hidden="true" />
      {EXTRA.map((item) => (
        <RailLink item={item} key={item.to} />
      ))}
      <span className="rsp" />
      <span
        className={cn("feed", running && "!bg-ok")}
        title={running ? "Tick feed running" : "Tick feed offline"}
      />
    </aside>
  )
}
