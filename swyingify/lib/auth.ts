import { betterAuth } from "better-auth"
import { nextCookies } from "better-auth/next-js"
import { Pool } from "pg"

const pool = new Pool({ connectionString: process.env.DATABASE_URL })
const baseURL = process.env.BETTER_AUTH_URL ?? "http://localhost:3000"
const secret = process.env.BETTER_AUTH_SECRET ?? "swyingify-local-development-secret-change-me-please"

const googleClientId = process.env.GOOGLE_CLIENT_ID
const googleClientSecret = process.env.GOOGLE_CLIENT_SECRET
const google = googleClientId && googleClientSecret
  ? { clientId: googleClientId, clientSecret: googleClientSecret }
  : undefined

export const auth = betterAuth({
  appName: "Swyingify",
  baseURL,
  secret,
  database: pool,
  emailAndPassword: {
    enabled: true,
    minPasswordLength: 8,
  },
  socialProviders: google ? { google } : undefined,
  trustedOrigins: [baseURL],
  plugins: [nextCookies()],
})

export type Session = typeof auth.$Infer.Session
