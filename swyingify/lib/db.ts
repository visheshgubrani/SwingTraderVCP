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

export const db = globalForDatabase.swyingifyPool ?? new Pool(poolConfig())

if (process.env.NODE_ENV !== "production") {
  globalForDatabase.swyingifyPool = db
}
