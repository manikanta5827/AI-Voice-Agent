// Shared Vercel AI SDK streaming measurement, used by both SDK runners
// (vercel-byok.js and vercel-gateway.js). Keeps the streamText/TTFT logic in
// one place so BYOK vs Gateway differ ONLY in how the model handle is built.

import { streamText } from "ai";
import { SYSTEM_PROMPT, USER_QUERY, MAX_TOKENS } from "./models.js";

// `model` is an AI SDK LanguageModel handle. Returns {ttft_ms,total_ms,chars,usageOut}.
export async function measureStream(model, providerOptions) {
  const t0 = performance.now();
  const result = streamText({
    model,
    system: SYSTEM_PROMPT,
    prompt: USER_QUERY,
    maxOutputTokens: MAX_TOKENS,
    providerOptions,
    maxRetries: 0, // fail fast — don't let 429 backoff (35s) pollute latency
  });

  let ttft = null, text = "";
  for await (const chunk of result.textStream) {
    if (ttft === null) ttft = performance.now() - t0;
    text += chunk;
  }
  const total = performance.now() - t0;
  if (ttft === null) ttft = total;

  let usageOut = null;
  try {
    const u = await result.usage;
    usageOut = u?.outputTokens ?? u?.completionTokens ?? null;
  } catch { /* usage not always available */ }

  return { ttft_ms: ttft, total_ms: total, chars: text.length, text: text.trim(), usageOut };
}
