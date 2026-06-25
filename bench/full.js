#!/usr/bin/env node
// COMBINED report: for EVERY model × EVERY stage, one streamed call captures
//   - TTFT (time to first token)
//   - total completion time
//   - the full Telugu output text
// so latency and language quality sit side-by-side per stage.
//
// Stages:
//   direct  = raw HTTP to the provider's native endpoint, own key
//   byok    = Vercel AI SDK, own provider key, no gateway
//   gateway = Vercel AI SDK via Vercel AI Gateway (one key)
//
//   node bench/full.js            # RUNS=2/model/stage for timing (text = run 1)
//   RUNS=3 node bench/full.js
//
// Thinking/reasoning DISABLED per provider (see models.js). Groq/Sarvam aren't on
// the gateway. Gemini direct/byok is throttled on this account → see gateway.

import { createOpenAI } from "@ai-sdk/openai";
import { createGoogleGenerativeAI } from "@ai-sdk/google";
import { createDeepSeek } from "@ai-sdk/deepseek";
import { createGroq } from "@ai-sdk/groq";
import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import { createGateway } from "ai";

import {
  loadEnv, RUNS, MODELS, PROVIDERS, SYSTEM_PROMPT, USER_QUERY, MAX_TOKENS,
  rawReasoningOff, sdkReasoningOff, needsCompletionTokens, median,
} from "./models.js";
import { measureStream } from "./_sdk.js";

loadEnv();
const RUNS_N = RUNS; // default 3; pass RUNS=2 to go faster

const gateway = process.env.AI_GATEWAY_API_KEY
  ? createGateway({ apiKey: process.env.AI_GATEWAY_API_KEY })
  : null;

function sdkFactory(provider) {
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

// --- direct stage: raw HTTP streaming, capture ttft/total/text ---
async function* sseLines(resp) {
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) { yield buf.slice(0, nl).trim(); buf = buf.slice(nl + 1); }
  }
  if (buf.trim()) yield buf.trim();
}

async function directOnce(m) {
  const { key, base } = PROVIDERS[m.provider];
  const apiKey = process.env[key];
  const body = {
    model: m.model,
    messages: [{ role: "system", content: SYSTEM_PROMPT }, { role: "user", content: USER_QUERY }],
    stream: true, stream_options: { include_usage: true },
    ...rawReasoningOff(m.provider, m.model),
  };
  if (needsCompletionTokens(m.model)) body.max_completion_tokens = MAX_TOKENS;
  else body.max_tokens = MAX_TOKENS;

  const t0 = performance.now();
  const resp = await fetch(`${base}/chat/completions`, {
    method: "POST",
    headers: { authorization: `Bearer ${apiKey}`, "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${(await resp.text()).slice(0, 90)}`);
  let ttft = null, text = "", out = null;
  for await (const line of sseLines(resp)) {
    if (!line.startsWith("data:")) continue;
    const data = line.slice(5).trim();
    if (data === "[DONE]") break;
    let ev; try { ev = JSON.parse(data); } catch { continue; }
    const tok = ev.choices?.[0]?.delta?.content;
    if (tok) { if (ttft === null) ttft = performance.now() - t0; text += tok; }
    if (ev.usage?.completion_tokens != null) out = ev.usage.completion_tokens;
  }
  const total = performance.now() - t0;
  if (ttft === null) ttft = total;
  return { ttft_ms: ttft, total_ms: total, text: text.trim(), usageOut: out };
}

// stage runner: returns {ttft,total,out,text} median-timed, or {skip}/{err}
async function runStage(stage, m) {
  // availability checks
  if (stage === "direct" || stage === "byok") {
    if (!process.env[PROVIDERS[m.provider].key]) return { skip: `no ${PROVIDERS[m.provider].key}` };
  }
  if (stage === "gateway") {
    if (!gateway) return { skip: "no AI_GATEWAY_API_KEY" };
    if (!PROVIDERS[m.provider].gwPrefix) return { skip: `${m.provider} not on gateway` };
  }

  const call = () => {
    if (stage === "direct") return directOnce(m);
    if (stage === "byok") return measureStream(sdkFactory(m.provider)(m.model), sdkReasoningOff(m.provider, m.model));
    return measureStream(gateway(PROVIDERS[m.provider].gwPrefix + m.model), sdkReasoningOff(m.provider, m.model));
  };

  const ttfts = [], totals = [];
  let out = null, text = "";
  for (let i = 0; i < RUNS_N; i++) {
    try {
      const r = await call();
      ttfts.push(r.ttft_ms); totals.push(r.total_ms);
      out = r.usageOut ?? out;
      if (i === 0) text = r.text || "(empty)";
    } catch (e) { return { err: String(e.message || e).slice(0, 90) }; }
  }
  return { ttft: median(ttfts), total: median(totals), out, text };
}

const fmt = (r) =>
  r.skip ? `SKIP (${r.skip})`
  : r.err ? `ERR ${r.err}`
  : `TTFT ${(r.ttft / 1000).toFixed(2)}s | total ${(r.total / 1000).toFixed(2)}s | out ${r.out ?? "?"} tok`;

async function main() {
  console.log(`\nCOMBINED latency + Telugu quality — ${RUNS_N} run(s)/model/stage, thinking=OFF`);
  console.log(`User: "${USER_QUERY}"\n`);
  const STAGES = ["direct", "byok", "gateway"];
  for (const m of MODELS) {
    console.log(`\n══════════ ${m.provider} ${m.model} ══════════`);
    for (const stage of STAGES) {
      const r = await runStage(stage, m);
      console.log(`  [${stage.padEnd(7)}] ${fmt(r)}`);
      if (!r.skip && !r.err) console.log(`            ↳ ${r.text.replace(/\n/g, " ")}`);
    }
  }
  console.log("");
}

main();
