import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderWithProviders, screen } from "@/test/test-utils"
import { TopBar } from "../TopBar"
import { Sidebar } from "../Sidebar"
import * as AdminApiModule from "@/features/admin/api"
import * as AuthApiModule from "@/features/auth/api"
import * as AuthContextModule from "@/features/auth/AuthContext"

describe("Sidebar Navigation Component", () => {
  it("renders all workstation navigation sections and links", () => {
    renderWithProviders(<Sidebar open={true} setOpen={vi.fn()} />)

    expect(screen.getByText("BBG // VCP TRADER")).toBeInTheDocument()
    expect(screen.getByText("ONLINE")).toBeInTheDocument()

    // Nav Links
    expect(screen.getByText("Workstation")).toBeInTheDocument()
    expect(screen.getByText("Stock Screener")).toBeInTheDocument()
    expect(screen.getByText("Trade Proposals")).toBeInTheDocument()
    expect(screen.getByText("Active Positions")).toBeInTheDocument()
    expect(screen.getByText("Order Book")).toBeInTheDocument()
    expect(screen.getByText("Tradebook")).toBeInTheDocument()
    expect(screen.getByText("Fundamentals")).toBeInTheDocument()
    expect(screen.getByText("Journal & AI")).toBeInTheDocument()
    expect(screen.getByText("Operations")).toBeInTheDocument()
    expect(screen.getByText("Account Ledger")).toBeInTheDocument()
  })

  it("toggles sidebar collapse/expand", async () => {
    const setOpen = vi.fn()
    const { user } = renderWithProviders(<Sidebar open={true} setOpen={setOpen} />)

    const collapseBtn = screen.getByRole("button", { name: /Collapse sidebar/i })
    await user.click(collapseBtn)

    expect(setOpen).toHaveBeenCalled()
  })
})

describe("TopBar Header Component", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(AuthContextModule, "useAppAuth").mockReturnValue({
      isAuthenticated: true,
      isLoading: false,
      error: null,
      login: vi.fn(),
      logout: vi.fn(),
    })
    vi.spyOn(AuthApiModule, "useAuthStatus").mockReturnValue({
      data: { healthy: true, expires_at: "2026-08-26T06:00:00Z" },
      isLoading: false,
    } as any)
    vi.spyOn(AuthApiModule, "useStartFyersLogin").mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as any)
  })

  it("renders brand logo, active ticker, and global controls", () => {
    vi.spyOn(AdminApiModule, "useKillSwitch").mockReturnValue({
      data: { enabled: false, reason: null },
      isLoading: false,
    } as any)
    vi.spyOn(AdminApiModule, "useSetKillSwitch").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any)

    renderWithProviders(
      <TopBar
        sidebarOpen={true}
        setSidebarOpen={vi.fn()}
        activeSymbol="NSE:RELIANCE-EQ"
        activeLtp={2540.5}
        activeTick={{
          symbol: "NSE:RELIANCE-EQ",
          ltp: 2540.5,
          change: 15.5,
          change_pct: 0.61,
          open: 2525.0,
          high: 2550.0,
          low: 2520.0,
          volume: 2500000,
        }}
      />
    )

    expect(screen.getByText("SWINGTRADER")).toBeInTheDocument()
    expect(screen.getByText("VCP")).toBeInTheDocument()
    expect(screen.getByText("NSE:RELIANCE-EQ")).toBeInTheDocument()
    expect(screen.getByText("₹2540.50")).toBeInTheDocument()
    expect(screen.getByText("ENGINE ENABLED")).toBeInTheDocument()
    expect(screen.getByText("FYERS CONNECTED")).toBeInTheDocument()
  })

  it("renders Kill Switch Active state when kill switch is engaged", () => {
    vi.spyOn(AdminApiModule, "useKillSwitch").mockReturnValue({
      data: { enabled: true, reason: "Operator pause" },
      isLoading: false,
    } as any)
    vi.spyOn(AdminApiModule, "useSetKillSwitch").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any)

    renderWithProviders(<TopBar sidebarOpen={true} setSidebarOpen={vi.fn()} />)

    expect(screen.getByText("KILL SWITCH ACTIVE")).toBeInTheDocument()
  })
})
