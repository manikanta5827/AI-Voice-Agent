#!/usr/bin/env node
// FILE 1 of 3 — DIRECT API benchmark.
// Each provider hit on its OWN native endpoint (OpenAI-compatible /chat/completions
// streaming) with the provider's own API key. No SDK, no gateway, no middle layer.
// Zero npm deps — Node 18+ built-in fetch.
//
//   node bench/direct.js          # 3 runs/model (RUNS=N to override)
//
// Every model runs with thinking/reasoning DISABLED (see models.js).

import {
  loadEnv, header, report, benchModel, MODELS, PROVIDERS,
  SYSTEM_PROMPT, USER_QUERY, MAX_TOKENS, rawReasoningOff, needsCompletionTokens,
} from "./models.js";

loadEnv();

async function* sseLines(resp) {
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      yield buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
    }
  }
  if (buf.trim()) yield buf.trim();
}

async function streamOnce(provider, model) {
  const { key, base } = PROVIDERS[provider];
  const apiKey = process.env[key];
  const body = {
    model,
    messages: [
      { role: "system", content: SYSTEM_PROMPT },
      { role: "user", content: USER_QUERY },
    ],
    stream: true,
    stream_options: { include_usage: true },
    ...rawReasoningOff(provider, model),
  };
  if (needsCompletionTokens(model)) body.max_completion_tokens = MAX_TOKENS;
  else body.max_tokens = MAX_TOKENS;

  const t0 = performance.now();
  const resp = await fetch(`${base}/chat/completions`, {
    method: "POST",
    headers: { authorization: `Bearer ${apiKey}`, "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${(await resp.text()).slice(0, 160)}`);

  let ttft = null, chars = 0, usageOut = null;
  for await (const line of sseLines(resp)) {
    if (!line.startsWith("data:")) continue;
    const data = line.slice(5).trim();
    if (data === "[DONE]") break;
    let ev;
    try { ev = JSON.parse(data); } catch { continue; }
    const tok = ev.choices?.[0]?.delta?.content;
    if (tok) {
      if (ttft === null) ttft = performance.now() - t0;
      chars += tok.length;
    }
    if (ev.usage?.completion_tokens != null) usageOut = ev.usage.completion_tokens;
  }
  const total = performance.now() - t0;
  if (ttft === null) ttft = total;
  return { ttft_ms: ttft, total_ms: total, chars, usageOut };
}

async function main() {
  header("DIRECT API (native endpoints, own keys)");
  const rows = [];
  for (const m of MODELS) {
    const skip = process.env[PROVIDERS[m.provider].key] ? null : `SKIP (no ${PROVIDERS[m.provider].key})`;
    const label = `${m.provider} ${m.model}`;
    const row = await benchModel(label, () => streamOnce(m.provider, m.model), skip);
    rows.push(row);
    console.log(`- ${label}: ${row.status || `TTFT ${(row.ttft / 1000).toFixed(2)}s | total ${(row.total / 1000).toFixed(2)}s | out ${row.outTok} tok`}`);
  }
  report("DIRECT API", rows);
}

main();
