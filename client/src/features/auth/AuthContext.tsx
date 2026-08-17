import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react"
import {
  apiRequest,
  clearAppSession,
  setOnUnauthorizedCallback,
  setStoredCsrfToken,
} from "@/lib/api"

interface SessionResponse {
  authenticated: boolean
  csrf_token?: string
  expires_at?: string
}

interface LoginResponse {
  status: string
  csrf_token: string
  expires_at: string
}

interface AuthContextType {
  isAuthenticated: boolean
  isLoading: boolean
  error: string | null
  login: (password: string) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextType | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(false)
  const [isLoading, setIsLoading] = useState<boolean>(true)
  const [error, setError] = useState<string | null>(null)

  const checkSession = useCallback(async () => {
    try {
      setIsLoading(true)
      setError(null)
      const res = await apiRequest<SessionResponse>("/auth/session")
      if (res.authenticated && res.csrf_token) {
        setStoredCsrfToken(res.csrf_token)
        setIsAuthenticated(true)
      } else {
        setIsAuthenticated(false)
      }
    } catch {
      setIsAuthenticated(false)
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    void checkSession()
  }, [checkSession])

  useEffect(() => {
    setOnUnauthorizedCallback(() => {
      setIsAuthenticated(false)
    })
    return () => {
      setOnUnauthorizedCallback(null)
    }
  }, [])

  const login = useCallback(async (password: string) => {
    setIsLoading(true)
    setError(null)
    try {
      const res = await apiRequest<LoginResponse>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ password }),
      })
      if (res.status === "ok") {
        setStoredCsrfToken(res.csrf_token)
        setIsAuthenticated(true)
      } else {
        throw new Error("Authentication failed")
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Invalid password"
      setError(msg)
      setIsAuthenticated(false)
      throw err
    } finally {
      setIsLoading(false)
    }
  }, [])

  const logout = useCallback(async () => {
    try {
      await apiRequest<{ status: string }>("/auth/logout", {
        method: "POST",
      })
    } catch {
      // Ignore network errors on logout
    } finally {
      clearAppSession()
      setIsAuthenticated(false)
    }
  }, [])

  return (
    <AuthContext.Provider
      value={{
        isAuthenticated,
        isLoading,
        error,
        login,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAppAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error("useAppAuth must be used within an AuthProvider")
  }
  return ctx
}
