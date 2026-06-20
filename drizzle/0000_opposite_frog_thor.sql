CREATE TABLE "calls" (
	"id" serial PRIMARY KEY NOT NULL,
	"call_sid" text NOT NULL,
	"stream_sid" text,
	"started_at" timestamp with time zone DEFAULT now(),
	"ended_at" timestamp with time zone,
	"duration_sec" integer,
	CONSTRAINT "calls_call_sid_unique" UNIQUE("call_sid")
);
--> statement-breakpoint
CREATE TABLE "messages" (
	"id" serial PRIMARY KEY NOT NULL,
	"call_sid" text NOT NULL,
	"role" text NOT NULL,
	"content" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now()
);
--> statement-breakpoint
ALTER TABLE "messages" ADD CONSTRAINT "messages_call_sid_calls_call_sid_fk" FOREIGN KEY ("call_sid") REFERENCES "public"."calls"("call_sid") ON DELETE cascade ON UPDATE no action;