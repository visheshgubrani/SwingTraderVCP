import "server-only"

import { createHmac } from "node:crypto"

import type { AccessContext, Feature } from "@/lib/access-types"

const TOKEN_TTL_SECONDS = 60

type InternalAccessClaims = {
  v: 1
  iss: "swyingify-next"
  aud: "swyingify-fastapi"
  sub: string | null
  features: Feature[]
  iat: number
  exp: number
}

function base64Url(value: string | Buffer): string {
  return Buffer.from(value).toString("base64url")
}

/**
 * Mint a short-lived service assertion from the already-resolved Better Auth
 * session. FastAPI accepts this assertion, never a caller-supplied user id.
 */
export function createInternalAccessToken(access: AccessContext): string | null {
  if (process.env.NODE_ENV !== "production") {
    return "development-bypass"
  }

  const secret = process.env.SAAS_INTERNAL_API_KEY?.trim()
  if (!secret || secret.length < 32) return null

  const now = Math.floor(Date.now() / 1000)
  const claims: InternalAccessClaims = {
    v: 1,
    iss: "swyingify-next",
    aud: "swyingify-fastapi",
    sub: access.userId,
    features: Object.entries(access.features)
      .filter(([, enabled]) => enabled)
      .map(([feature]) => feature as Feature),
    iat: now,
    exp: now + TOKEN_TTL_SECONDS,
  }
  const payload = base64Url(JSON.stringify(claims))
  const signature = createHmac("sha256", secret).update(payload).digest("base64url")
  return `${payload}.${signature}`
}
