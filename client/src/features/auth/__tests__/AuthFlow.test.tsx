import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderWithProviders, screen } from "@/test/test-utils"
import { LoginPage } from "../LoginPage"
import { AuthBanner } from "../AuthBanner"
import * as AuthContextModule from "../AuthContext"
import * as AuthApiModule from "../api"

describe("LoginPage UI Flow", () => {
  it("renders workstation login elements correctly", () => {
    vi.spyOn(AuthContextModule, "useAppAuth").mockReturnValue({
      isAuthenticated: false,
      isLoading: false,
      error: null,
      login: vi.fn(),
      logout: vi.fn(),
    })

    renderWithProviders(<LoginPage />)

    expect(screen.getByText("SwingTraderVCP")).toBeInTheDocument()
    expect(screen.getByText(/Personal Trading Workstation/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/Workstation Password/i)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Sign In to Workstation/i })).toBeInTheDocument()
  })

  it("toggles password visibility when eye button is clicked", async () => {
    vi.spyOn(AuthContextModule, "useAppAuth").mockReturnValue({
      isAuthenticated: false,
      isLoading: false,
      error: null,
      login: vi.fn(),
      logout: vi.fn(),
    })

    const { user } = renderWithProviders(<LoginPage />)
    const passwordInput = screen.getByLabelText(/Workstation Password/i)

    expect(passwordInput).toHaveAttribute("type", "password")

    // Click toggle visibility button
    const toggleBtn = screen.getByRole("button", { name: "" })
    await user.click(toggleBtn)
    expect(passwordInput).toHaveAttribute("type", "text")

    await user.click(toggleBtn)
    expect(passwordInput).toHaveAttribute("type", "password")
  })

  it("submits password and invokes login function", async () => {
    const mockLogin = vi.fn().mockResolvedValue(undefined)
    vi.spyOn(AuthContextModule, "useAppAuth").mockReturnValue({
      isAuthenticated: false,
      isLoading: false,
      error: null,
      login: mockLogin,
      logout: vi.fn(),
    })

    const { user } = renderWithProviders(<LoginPage />)
    const passwordInput = screen.getByLabelText(/Workstation Password/i)
    const submitBtn = screen.getByRole("button", { name: /Sign In to Workstation/i })

    await user.type(passwordInput, "secret123")
    await user.click(submitBtn)

    expect(mockLogin).toHaveBeenCalledWith("secret123")
  })

  it("displays loading state while authenticating", () => {
    vi.spyOn(AuthContextModule, "useAppAuth").mockReturnValue({
      isAuthenticated: false,
      isLoading: true,
      error: null,
      login: vi.fn(),
      logout: vi.fn(),
    })

    renderWithProviders(<LoginPage />)

    expect(screen.getByText(/Authenticating.../i)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Authenticating.../i })).toBeDisabled()
  })

  it("displays error alert when login fails", () => {
    vi.spyOn(AuthContextModule, "useAppAuth").mockReturnValue({
      isAuthenticated: false,
      isLoading: false,
      error: "Invalid workstation credentials.",
      login: vi.fn(),
      logout: vi.fn(),
    })

    renderWithProviders(<LoginPage />)

    expect(screen.getByText("Invalid workstation credentials.")).toBeInTheDocument()
  })
})

describe("AuthBanner Broker Status Flow", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it("does not render when auth is healthy", () => {
    vi.spyOn(AuthApiModule, "useAuthStatus").mockReturnValue({
      data: { healthy: true, reason: null },
      isLoading: false,
      error: null,
    } as any)
    vi.spyOn(AuthApiModule, "useAuthEvents").mockReturnValue({
      data: [],
      isLoading: false,
    } as any)
    vi.spyOn(AuthApiModule, "useStartFyersLogin").mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as any)

    const { container } = renderWithProviders(<AuthBanner />)
    expect(container).toBeEmptyDOMElement()
  })

  it("renders warning banner when Fyers token is expired", async () => {
    const mockMutate = vi.fn()
    vi.spyOn(AuthApiModule, "useAuthStatus").mockReturnValue({
      data: { healthy: false, reason: "expired" },
      isLoading: false,
      error: null,
    } as any)
    vi.spyOn(AuthApiModule, "useAuthEvents").mockReturnValue({
      data: [],
      isLoading: false,
    } as any)
    vi.spyOn(AuthApiModule, "useStartFyersLogin").mockReturnValue({
      mutate: mockMutate,
      isPending: false,
    } as any)

    const { user } = renderWithProviders(<AuthBanner />)

    expect(screen.getByText(/Market data authentication required/i)).toBeInTheDocument()
    expect(screen.getByText(/The Fyers token has expired/i)).toBeInTheDocument()

    const loginBtn = screen.getByRole("button", { name: /Login to Fyers/i })
    expect(loginBtn).toBeInTheDocument()

    await user.click(loginBtn)
    expect(mockMutate).toHaveBeenCalled()
  })
})
