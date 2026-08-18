import { env } from "cloudflare:workers";
import { drizzle } from "drizzle-orm/d1";
import * as schema from "./schema";

export function getD1(): D1Database {
  const database = env.DB;
  if (!database) {
    throw new Error(
      "Cloudflare D1 binding `DB` is unavailable. Configure the Sites D1 binding before using the console.",
    );
  }
  return database;
}

export function getDb(database: D1Database = getD1()) {
  return drizzle(database, { schema });
}
