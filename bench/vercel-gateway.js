#!/usr/bin/env node
// FILE 3 of 3 — VERCEL AI SDK, AI GATEWAY.
// Uses the Vercel AI SDK routed through the Vercel AI Gateway (OpenRouter-style:
// add credits, call any provider/model by `provider/model` id with ONE key).
// Auth via AI_GATEWAY_API_KEY. Same models as direct.js/vercel-byok.js, so the
// three files give a clean direct-vs-BYOK-vs-gateway latency comparison.
//
//   node bench/vercel-gateway.js  # 3 runs/model (RUNS=N to override)
//
// Providers not on the gateway (Groq LPU, Sarvam) are skipped here.
// Thinking/reasoning DISABLED per provider via providerOptions (see models.js).

import { createGateway } from "ai";

import {
  loadEnv, header, report, benchModel, MODELS, PROVIDERS, sdkReasoningOff,
} from "./models.js";
import { measureStream } from "./_sdk.js";

loadEnv();

const gateway = createGateway({ apiKey: process.env.AI_GATEWAY_API_KEY });

async function main() {
  header("VERCEL AI SDK — AI GATEWAY (one key, all providers)");
  const rows = [];
  for (const m of MODELS) {
    const { gwPrefix } = PROVIDERS[m.provider];
    const label = `GW ${m.provider} ${m.model}`;
    let skip = null;
    if (!process.env.AI_GATEWAY_API_KEY) skip = "SKIP (no AI_GATEWAY_API_KEY)";
    else if (!gwPrefix) skip = `SKIP (${m.provider} not on Vercel gateway)`;
    const gwId = gwPrefix ? gwPrefix + m.model : m.model;
    const row = await benchModel(
      label,
      () => measureStream(gateway(gwId), sdkReasoningOff(m.provider, m.model)),
      skip,
    );
    rows.push(row);
    console.log(`- ${label}: ${row.status || `TTFT ${(row.ttft / 1000).toFixed(2)}s | total ${(row.total / 1000).toFixed(2)}s | out ${row.outTok} tok`}`);
  }
  report("VERCEL AI SDK GATEWAY", rows);
}

main();
