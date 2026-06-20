import { pgTable, serial, text, timestamp, integer } from "drizzle-orm/pg-core";

export const calls = pgTable("calls", {
  id:          serial("id").primaryKey(),
  callSid:     text("call_sid").unique().notNull(),
  streamSid:   text("stream_sid"),
  startedAt:   timestamp("started_at", { withTimezone: true }).defaultNow(),
  endedAt:     timestamp("ended_at",   { withTimezone: true }),
  durationSec: integer("duration_sec"),
});

export const messages = pgTable("messages", {
  id:        serial("id").primaryKey(),
  callSid:   text("call_sid").notNull().references(() => calls.callSid, { onDelete: "cascade" }),
  role:      text("role", { enum: ["user", "assistant"] }).notNull(),
  content:   text("content").notNull(),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
});
