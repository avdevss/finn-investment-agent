// The one Postgres client for every server component and server action.
import "server-only";
import postgres from "postgres";

const url = process.env.DATABASE_URL;
if (!url) throw new Error("DATABASE_URL is not set in web/.env.local");

const globalForSql = globalThis as unknown as { sql?: postgres.Sql };

// Reused across dev hot reloads, which would otherwise open a new pool per edit.
export const sql: postgres.Sql =
  globalForSql.sql ??
  postgres(url, {
    ssl: "require",
    max: 5,
    idle_timeout: 20,
    connect_timeout: 10,
    // The Supabase pooler cannot hold prepared statements across clients.
    prepare: false,
  });

if (process.env.NODE_ENV !== "production") globalForSql.sql = sql;
