import { NavLink, useLocation } from "react-router"

import { useScanRuns } from "@/features/screener/api"
import { cn } from "@/lib/utils"

interface StageTabProps {
  to: string
  label: string
  count: number | null
}

function StageTab({ to, label, count }: StageTabProps) {
  return (
    <NavLink
      aria-selected={undefined}
      className={({ isActive }) => cn("tab", isActive && "on")}
      end={to === "/"}
      role="tab"
      to={to}
    >
      {label}
      {count !== null && <span className="tcount">{count}</span>}
    </NavLink>
  )
}

const CORE_MODULES = ["/", "/scanner", "/positions", "/orders", "/tradebook"]

/** Stage module tabs (design .tabs) — visible on the five core module routes. */
export function StageNav({
  positionsCount,
  orderIntentsCount,
}: {
  positionsCount: number
  orderIntentsCount: number
}) {
  const location = useLocation()
  const scanRuns = useScanRuns()

  const activeBase = CORE_MODULES.find((base) =>
    base === "/" ? location.pathname === "/" : location.pathname.startsWith(base),
  )
  if (!activeBase) return null

  const latestRun = scanRuns.data?.find((run) => run.status === "succeeded")
  const scannerCount = latestRun?.passing_count ?? null

  const tabs: StageTabProps[] = [
    { to: "/", label: "Chart", count: null },
    { to: "/scanner", label: "Scanner", count: scannerCount },
    { to: "/positions", label: "Positions", count: positionsCount },
    { to: "/orders", label: "Order intents", count: orderIntentsCount },
    { to: "/tradebook", label: "Tradebook", count: null },
  ]

  return (
    <nav aria-label="Workspace modules" className="tabs" role="tablist">
      {tabs.map((tab) => (
        <StageTab count={tab.count} key={tab.to} label={tab.label} to={tab.to} />
      ))}
    </nav>
  )
}
