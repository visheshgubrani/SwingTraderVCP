import type { Dispatch, SetStateAction } from "react"
import {
  BookCheckIcon,
  BookOpenIcon,
  BriefcaseBusinessIcon,
  CandlestickChartIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  LandmarkIcon,
  ListOrderedIcon,
  ScanSearchIcon,
  SlidersHorizontalIcon,
  SparklesIcon,
  TablePropertiesIcon,
} from "lucide-react"
import { NavLink } from "react-router"

import { cn } from "@/lib/utils"

interface SidebarProps {
  open: boolean
  setOpen: Dispatch<SetStateAction<boolean>>
}

interface NavGroup {
  category: string
  items: ReadonlyArray<{
    to: string
    label: string
    icon: typeof CandlestickChartIcon
    end?: boolean
    badge?: string
  }>
}

const navGroups: ReadonlyArray<NavGroup> = [
  {
    category: "CORE TERMINAL",
    items: [
      { to: "/", label: "Workstation", icon: CandlestickChartIcon, end: true },
      { to: "/scanner", label: "Stock Screener", icon: TablePropertiesIcon, badge: "EOD" },
      { to: "/proposals", label: "Trade Proposals", icon: SparklesIcon, badge: "AI" },
    ],
  },
  {
    category: "MONITOR & ORDERS",
    items: [
      { to: "/positions", label: "Active Positions", icon: BriefcaseBusinessIcon },
      { to: "/orders", label: "Order Book", icon: ListOrderedIcon },
      { to: "/tradebook", label: "Tradebook", icon: BookCheckIcon },
    ],
  },
  {
    category: "RESEARCH & INTEL",
    items: [
      { to: "/fundamentals", label: "Fundamentals", icon: ScanSearchIcon },
      { to: "/journal", label: "Journal & AI", icon: BookOpenIcon },
    ],
  },
  {
    category: "SYSTEM",
    items: [
      { to: "/operations", label: "Operations", icon: SlidersHorizontalIcon },
      { to: "/ledger", label: "Account Ledger", icon: LandmarkIcon },
    ],
  },
]

export function Sidebar({ open, setOpen }: SidebarProps) {
  return (
    <aside
      className={cn(
        "z-20 flex shrink-0 flex-col justify-between border-r border-border bg-card/95 font-mono text-xs text-card-foreground select-none transition-[width] duration-200 shadow-md",
        open ? "w-56" : "w-14",
      )}
    >
      <div className="flex flex-col gap-4 overflow-y-auto p-2 scrollbar-none">
        {/* Terminal Header Info */}
        {open ? (
          <div className="flex items-center justify-between border-b border-border/60 px-2 py-2 text-[10px] tracking-wider text-muted-foreground uppercase">
            <span className="font-bold text-foreground/90">BBG // VCP TRADER</span>
            <span className="flex items-center gap-1 font-mono text-[9px] text-emerald-500 font-semibold">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
              ONLINE
            </span>
          </div>
        ) : (
          <div className="flex justify-center border-b border-border/60 py-2">
            <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" title="System Online" />
          </div>
        )}

        <nav aria-label="Primary navigation" className="flex flex-col gap-4">
          {navGroups.map((group) => (
            <div key={group.category} className="flex flex-col gap-1">
              {open && (
                <div className="px-2 pb-1 text-[9px] font-bold tracking-widest text-muted-foreground/70 uppercase">
                  {group.category}
                </div>
              )}
              {group.items.map(({ to, label, icon: Icon, end, badge }) => (
                <NavLink
                  className={({ isActive }) =>
                    cn(
                      "relative flex h-8.5 items-center gap-2.5 rounded-sm px-2 text-[11px] font-medium transition-all duration-150",
                      isActive
                        ? "bg-accent/90 text-accent-foreground font-semibold shadow-xs border-l-2 border-primary"
                        : "text-muted-foreground hover:bg-accent/40 hover:text-foreground",
                    )
                  }
                  end={end}
                  key={to}
                  title={!open ? label : undefined}
                  to={to}
                >
                  <Icon aria-hidden="true" className="size-4 shrink-0 opacity-85" />
                  {open && (
                    <div className="flex min-w-0 flex-1 items-center justify-between gap-1.5">
                      <span className="truncate">{label}</span>
                      {badge && (
                        <span className="rounded bg-primary/20 px-1 py-0.2 text-[9px] font-bold text-primary">
                          {badge}
                        </span>
                      )}
                    </div>
                  )}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
      </div>

      <div className="border-t border-border/80 p-2 bg-card/60">
        <button
          aria-label={open ? "Collapse sidebar" : "Expand sidebar"}
          className="flex h-8 w-full items-center justify-center gap-2 rounded-sm text-[11px] font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          onClick={() => setOpen((current) => !current)}
          type="button"
        >
          {open ? (
            <>
              <ChevronLeftIcon aria-hidden="true" className="size-3.5" />
              <span>COLLAPSE</span>
            </>
          ) : (
            <ChevronRightIcon aria-hidden="true" className="size-3.5" />
          )}
        </button>
      </div>
    </aside>
  )
}
