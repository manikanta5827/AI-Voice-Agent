import { drizzle } from "drizzle-orm/bun-sql";
import { sql, eq, isNull, and } from "drizzle-orm";
import * as schema from "./schema.ts";
import { calls, messages } from "./schema.ts";

const db = drizzle({ connection: Bun.env.DATABASE_URL!, schema });

export async function initDB(): Promise<void> {
  await db.execute(sql`SELECT 1`);
  console.log("✅ DB: Connected to Supabase");
}

export async function insertCall(callSid: string, streamSid: string): Promise<void> {
  await db.insert(calls).values({ callSid, streamSid }).onConflictDoNothing();
}

export async function insertMessage(callSid: string, role: "user" | "assistant", content: string): Promise<void> {
  await db.insert(messages).values({ callSid, role, content });
}

export async function endCall(callSid: string): Promise<void> {
  await db
    .update(calls)
    .set({
      endedAt:     sql`NOW()`,
      durationSec: sql`EXTRACT(EPOCH FROM (NOW() - started_at))::INTEGER`,
    })
    .where(and(eq(calls.callSid, callSid), isNull(calls.endedAt)));
}
