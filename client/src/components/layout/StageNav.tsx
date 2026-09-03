import { NavLink } from "react-router"

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

/** Stage module tabs (design .tabs) — docked at the bottom of the workstation stage. */
export function StageNav({
  positionsCount,
  orderIntentsCount,
}: {
  positionsCount: number
  orderIntentsCount: number
}) {
  const scanRuns = useScanRuns()

  const latestRun = scanRuns.data?.find((run) => run.status === "succeeded")
  const scannerCount = latestRun?.passing_count ?? null

  const tabs: StageTabProps[] = [
    { to: "/", label: "Chart", count: null },
    { to: "/scanner", label: "Scanner", count: scannerCount },
    { to: "/proposals", label: "Proposals", count: null },
    { to: "/positions", label: "Positions", count: positionsCount },
    { to: "/orders", label: "Orders", count: orderIntentsCount },
    { to: "/tradebook", label: "Tradebook", count: null },
    { to: "/journal", label: "Journal", count: null },
    { to: "/operations", label: "Operations", count: null },
  ]

  return (
    <nav aria-label="Workspace modules" className="tabs" role="tablist">
      {tabs.map((tab) => (
        <StageTab count={tab.count} key={tab.to} label={tab.label} to={tab.to} />
      ))}
    </nav>
  )
}
