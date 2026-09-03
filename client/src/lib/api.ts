export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1"

const CSRF_TOKEN_KEY = "swing_csrf_token"

let onUnauthorizedCallback: (() => void) | null = null

export function setOnUnauthorizedCallback(cb: (() => void) | null) {
  onUnauthorizedCallback = cb
}

export function getStoredCsrfToken(): string | null {
  try {
    return window.sessionStorage.getItem(CSRF_TOKEN_KEY)
  } catch {
    return null
  }
}

export function setStoredCsrfToken(csrf: string | null) {
  try {
    if (csrf) {
      window.sessionStorage.setItem(CSRF_TOKEN_KEY, csrf)
    } else {
      window.sessionStorage.removeItem(CSRF_TOKEN_KEY)
    }
  } catch {
    // Ignore storage quota errors
  }
}

export function clearAppSession() {
  setStoredCsrfToken(null)
}

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = "ApiError"
    this.status = status
  }
}

const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"])

export async function apiRequest<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase()
  const headers = new Headers(init?.headers)

  if (!headers.has("Content-Type") && !(init?.body instanceof FormData)) {
    headers.set("Content-Type", "application/json")
  }

  // Attach session-bound CSRF token on mutating requests (SEC-001)
  if (MUTATING_METHODS.has(method)) {
    const csrf = getStoredCsrfToken()
    if (csrf) {
      headers.set("X-CSRF-Token", csrf)
    }
  }

  // Authentication is cookie-only (HttpOnly; SameSite=Lax; Secure)
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers,
  })

  if (response.status === 401 && !path.includes("/auth/login")) {
    clearAppSession()
    if (onUnauthorizedCallback) {
      onUnauthorizedCallback()
    }
  }

  if (!response.ok) {
    let message = `Request failed with status ${response.status}.`
    try {
      const body = (await response.json()) as {
        detail?: string | Array<{ msg?: string }>
      }
      if (typeof body.detail === "string") {
        message = body.detail
      } else if (Array.isArray(body.detail)) {
        message = body.detail
          .map((item) => item.msg)
          .filter(Boolean)
          .join(" ")
      }
    } catch {
      // Keep the status-based fallback for non-JSON failures.
    }
    throw new ApiError(response.status, message)
  }

  // 204 No Content and other empty bodies are valid (e.g. DELETE endpoints).
  const body = await response.text()
  if (!body) return undefined as T
  try {
    return JSON.parse(body) as T
  } catch {
    throw new ApiError(response.status, `Invalid JSON response (${response.status}).`)
  }
}
