#!/usr/bin/env node
// Averaged latency across N INDEPENDENT samples per model per stage, to wash out
// network jitter. Reports avg, median, and MIN (min ≈ pure compute, no network
// noise) for TTFT and total. Latency only — Telugu quality is captured in
// quality.js / full.txt (and is transport-invariant).
//
//   node bench/avg.js            # SAMPLES=3 (pass SAMPLES=5 for more)
//
// Stages: direct (raw HTTP), byok (AI SDK + own key), gateway (AI SDK + gateway).
// Thinking/reasoning DISABLED per provider (models.js).

import { createOpenAI } from "@ai-sdk/openai";
import { createGoogleGenerativeAI } from "@ai-sdk/google";
import { createDeepSeek } from "@ai-sdk/deepseek";
import { createGroq } from "@ai-sdk/groq";
import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import { createGateway } from "ai";

import {
  loadEnv, MODELS, PROVIDERS, SYSTEM_PROMPT, USER_QUERY, MAX_TOKENS,
  rawReasoningOff, sdkReasoningOff, needsCompletionTokens, median,
} from "./models.js";
import { measureStream } from "./_sdk.js";

loadEnv();
const SAMPLES = parseInt(process.env.SAMPLES || "3", 10);
const avg = (a) => a.reduce((s, x) => s + x, 0) / a.length;

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
    headers: { authorization: `Bearer ${process.env[key]}`, "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  let ttft = null;
  for await (const line of sseLines(resp)) {
    if (!line.startsWith("data:")) continue;
    const data = line.slice(5).trim();
    if (data === "[DONE]") break;
    let ev; try { ev = JSON.parse(data); } catch { continue; }
    if (ev.choices?.[0]?.delta?.content && ttft === null) ttft = performance.now() - t0;
  }
  const total = performance.now() - t0;
  return { ttft_ms: ttft ?? total, total_ms: total };
}

function call(stage, m) {
  if (stage === "direct") return directOnce(m);
  if (stage === "byok") return measureStream(sdkFactory(m.provider)(m.model), sdkReasoningOff(m.provider, m.model));
  return measureStream(gateway(PROVIDERS[m.provider].gwPrefix + m.model), sdkReasoningOff(m.provider, m.model));
}

function available(stage, m) {
  if (stage === "gateway") {
    if (!gateway) return "no AI_GATEWAY_API_KEY";
    if (!PROVIDERS[m.provider].gwPrefix) return `${m.provider} not on gateway`;
  } else if (!process.env[PROVIDERS[m.provider].key]) {
    return `no ${PROVIDERS[m.provider].key}`;
  }
  return null;
}

async function sampleStage(stage, m) {
  const skip = available(stage, m);
  if (skip) return { skip };
  const ttfts = [], totals = [];
  let lastErr = null;
  for (let i = 0; i < SAMPLES; i++) {
    try { const r = await call(stage, m); ttfts.push(r.ttft_ms); totals.push(r.total_ms); }
    catch (e) { lastErr = String(e.message || e).slice(0, 50); }
  }
  if (!ttfts.length) return { err: lastErr || "all failed" };
  return {
    n: ttfts.length,
    ttftAvg: avg(ttfts), ttftMed: median(ttfts), ttftMin: Math.min(...ttfts),
    totAvg: avg(totals), totMed: median(totals), totMin: Math.min(...totals),
  };
}

const s = (ms) => (ms / 1000).toFixed(2);

async function main() {
  console.log(`\nAVERAGED latency — ${SAMPLES} independent samples/model/stage, thinking=OFF`);
  console.log(`User: "${USER_QUERY}"\n`);

  const STAGES = ["direct", "byok", "gateway"];
  for (const stage of STAGES) {
    const rows = [];
    for (const m of MODELS) rows.push({ label: `${m.provider} ${m.model}`, ...(await sampleStage(stage, m)) });

    console.log(`\n========== STAGE: ${stage.toUpperCase()} ==========`);
    const pad = (x, n) => String(x).padEnd(n);
    console.log(pad("MODEL", 42), pad("TTFT avg/med/min", 24), pad("TOTAL avg/med/min", 24), "n");
    const ok = rows.filter((r) => r.ttftMed != null).sort((a, b) => a.ttftMed - b.ttftMed);
    const bad = rows.filter((r) => r.ttftMed == null);
    for (const r of ok)
      console.log(
        pad(r.label, 42),
        pad(`${s(r.ttftAvg)}/${s(r.ttftMed)}/${s(r.ttftMin)}s`, 24),
        pad(`${s(r.totAvg)}/${s(r.totMed)}/${s(r.totMin)}s`, 24),
        r.n,
      );
    for (const r of bad) console.log(pad(r.label, 42), r.skip ? `SKIP (${r.skip})` : `ERR ${r.err}`);
  }
  console.log("");
}

main();
