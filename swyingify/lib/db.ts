import "server-only"

import { Pool } from "pg"

const databaseUrl = process.env.DATABASE_URL

const globalForDatabase = globalThis as typeof globalThis & {
  swyingifyPool?: Pool
}

export const db =
  globalForDatabase.swyingifyPool ??
  new Pool({
    connectionString: databaseUrl,
    max: 10,
  })

if (process.env.NODE_ENV !== "production") {
  globalForDatabase.swyingifyPool = db
}
