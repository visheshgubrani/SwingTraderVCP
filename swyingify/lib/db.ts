import "server-only"

import { Pool, type PoolConfig } from "pg"

function poolConfig(): PoolConfig {
  const host = process.env.POSTGRES_HOST
  if (host) {
    const port = Number(process.env.POSTGRES_PORT ?? "5432")
    if (!Number.isFinite(port) || port <= 0) {
      throw new Error(`Invalid POSTGRES_PORT: ${process.env.POSTGRES_PORT}`)
    }
    const password = process.env.POSTGRES_PASSWORD
    if (password === undefined) {
      throw new Error("POSTGRES_PASSWORD is required when POSTGRES_HOST is set")
    }
    return {
      host,
      port,
      user: process.env.POSTGRES_USER ?? "algo",
      password,
      database: process.env.POSTGRES_DB ?? "algo_trading",
      max: 10,
    }
  }

  const databaseUrl = process.env.DATABASE_URL
  if (!databaseUrl) {
    throw new Error("DATABASE_URL or POSTGRES_HOST must be set")
  }
  return {
    connectionString: databaseUrl,
    max: 10,
  }
}

const globalForDatabase = globalThis as typeof globalThis & {
  swyingifyPool?: Pool
}

let pool: Pool | undefined

function getPool(): Pool {
  if (!pool) {
    pool = globalForDatabase.swyingifyPool ?? new Pool(poolConfig())
    if (process.env.NODE_ENV !== "production") {
      globalForDatabase.swyingifyPool = pool
    }
  }
  return pool
}

/**
 * Lazy pool: `next build` imports auth/entitlements without DB env.
 * Validation and connection happen on first real use at runtime.
 */
export const db: Pool = new Proxy({} as Pool, {
  get(_target, prop, receiver) {
    const value = Reflect.get(getPool(), prop, receiver)
    return typeof value === "function" ? value.bind(getPool()) : value
  },
})
