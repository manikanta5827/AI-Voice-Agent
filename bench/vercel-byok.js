#!/usr/bin/env node
// FILE 2 of 3 — VERCEL AI SDK, BRING-YOUR-OWN-KEY.
// Uses the Vercel AI SDK (`ai` + per-provider `@ai-sdk/*` packages) but talks
// straight to each provider with YOUR OWN provider API key — NO Vercel gateway,
// no gateway credits. This isolates the SDK overhead vs the raw fetch in
// direct.js, while still hitting the providers directly.
//
//   node bench/vercel-byok.js     # 3 runs/model (RUNS=N to override)
//
// Thinking/reasoning DISABLED per provider via providerOptions (see models.js).

import { createOpenAI } from "@ai-sdk/openai";
import { createGoogleGenerativeAI } from "@ai-sdk/google";
import { createDeepSeek } from "@ai-sdk/deepseek";
import { createGroq } from "@ai-sdk/groq";
import { createOpenAICompatible } from "@ai-sdk/openai-compatible";

import {
  loadEnv, header, report, benchModel, MODELS, PROVIDERS, sdkReasoningOff,
} from "./models.js";
import { measureStream } from "./_sdk.js";

loadEnv();

// Build a provider factory bound to the user's own key for that provider.
function factoryFor(provider) {
  const apiKey = process.env[PROVIDERS[provider].key];
  switch (provider) {
    case "openai":   return createOpenAI({ apiKey });
    case "google":   return createGoogleGenerativeAI({ apiKey });
    case "deepseek": return createDeepSeek({ apiKey });
    case "groq":     return createGroq({ apiKey });
    case "sarvam":   return createOpenAICompatible({ name: "sarvam", apiKey, baseURL: PROVIDERS.sarvam.base });
    default:         return null;
  }
}

async function main() {
  header("VERCEL AI SDK — BYOK (own keys, no gateway)");
  const rows = [];
  for (const m of MODELS) {
    const label = `${m.provider} ${m.model}`;
    const skip = process.env[PROVIDERS[m.provider].key] ? null : `SKIP (no ${PROVIDERS[m.provider].key})`;
    const row = await benchModel(
      label,
      () => measureStream(factoryFor(m.provider)(m.model), sdkReasoningOff(m.provider, m.model)),
      skip,
    );
    rows.push(row);
    console.log(`- ${label}: ${row.status || `TTFT ${(row.ttft / 1000).toFixed(2)}s | total ${(row.total / 1000).toFixed(2)}s | out ${row.outTok} tok`}`);
  }
  report("VERCEL AI SDK BYOK", rows);
}

main();
