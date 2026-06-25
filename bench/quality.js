#!/usr/bin/env node
// Telugu OUTPUT QUALITY capture — prints the full response text per model so the
// actual Telugu can be judged (latency benches threw the text away).
//
//   node bench/quality.js
//
// Output text is transport-invariant: the same model + same params returns the
// same text whether called direct, via Vercel BYOK, or via the gateway. So this
// captures ONCE per model (raw streaming). Gemini is routed through the gateway
// base because the direct Gemini key is throttled/capped on this account.
//
// Thinking/reasoning DISABLED per provider (same config as the latency benches).

import {
  loadEnv, MODELS, PROVIDERS, SYSTEM_PROMPT, USER_QUERY, MAX_TOKENS,
  rawReasoningOff, needsCompletionTokens,
} from "./models.js";

loadEnv();

const GW = "https://ai-gateway.vercel.sh/v1";

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

// Returns {base, key, model} — Gemini goes via gateway (direct key capped).
function route(m) {
  if (m.provider === "google") {
    return { base: GW, key: "AI_GATEWAY_API_KEY", model: PROVIDERS.google.gwPrefix + m.model, via: "gateway" };
  }
  return { base: PROVIDERS[m.provider].base, key: PROVIDERS[m.provider].key, model: m.model, via: "direct" };
}

async function getText(m) {
  const r = route(m);
  const apiKey = process.env[r.key];
  if (!apiKey) return { text: `(SKIP — no ${r.key})`, via: r.via };
  const body = {
    model: r.model,
    messages: [
      { role: "system", content: SYSTEM_PROMPT },
      { role: "user", content: USER_QUERY },
    ],
    stream: true,
    ...rawReasoningOff(m.provider, m.model),
  };
  if (needsCompletionTokens(m.model)) body.max_completion_tokens = MAX_TOKENS;
  else body.max_tokens = MAX_TOKENS;

  const resp = await fetch(`${r.base}/chat/completions`, {
    method: "POST",
    headers: { authorization: `Bearer ${apiKey}`, "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) return { text: `(ERR HTTP ${resp.status}: ${(await resp.text()).slice(0, 120)})`, via: r.via };

  let text = "";
  for await (const line of sseLines(resp)) {
    if (!line.startsWith("data:")) continue;
    const data = line.slice(5).trim();
    if (data === "[DONE]") break;
    let ev; try { ev = JSON.parse(data); } catch { continue; }
    const tok = ev.choices?.[0]?.delta?.content;
    if (tok) text += tok;
  }
  return { text: text.trim() || "(empty)", via: r.via };
}

async function main() {
  console.log(`\nTELUGU OUTPUT QUALITY — one response/model, thinking=OFF`);
  console.log(`User asked: "${USER_QUERY}"\n`);
  for (const m of MODELS) {
    const { text, via } = await getText(m);
    console.log(`\n━━━ ${m.provider} ${m.model}  [${via}] ━━━`);
    console.log(text);
  }
  console.log("");
}

main();
