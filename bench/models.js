// Shared model matrix + helpers for all three benchmark runners
// (direct.js, vercel-byok.js, vercel-gateway.js).
//
// Single source of truth so the three transports test the EXACT same models.
// Every model runs with reasoning/thinking DISABLED — that is the whole point
// of the comparison for a latency-sensitive voice agent. The disable mechanism
// differs per provider, so it lives here once:
//   - deepseek : body `thinking: {type:"disabled"}` (default is ON → slow)
//   - google   : raw `reasoning_effort:"none"` / SDK thinkingBudget 0
//   - groq     : `reasoning_effort:"none"` (qwen3 / gpt-oss think by default)
//   - openai   : only the gpt-5*-mini reasoners need `reasoning_effort:"minimal"`;
//                gpt-4* and gpt-5*-chat-latest are already non-reasoning
//   - sarvam   : non-reasoning, nothing to disable

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dir = dirname(fileURLToPath(import.meta.url));

export const RUNS = parseInt(process.env.RUNS || "3", 10);
export const MAX_TOKENS = 256; // matches the bot's short-reply profile

// --- load .env (no dotenv dependency) -------------------------------------
export function loadEnv() {
  try {
    const txt = readFileSync(join(__dir, "..", ".env"), "utf8");
    for (const line of txt.split("\n")) {
      const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
      if (m && !(m[1] in process.env)) process.env[m[1]] = m[2].replace(/^["']|["']$/g, "");
    }
  } catch {
    /* no .env — rely on real env */
  }
}

export const SYSTEM_PROMPT = readFileSync(join(__dir, "system_prompt.txt"), "utf8");
// Realistic spoken-Telugu user turn (from a real call transcript).
export const USER_QUERY = "నాకు ఇన్సూరెన్స్ గురించి తెలుసుకోవాలండి. దాని గురించి ఏమైనా చెప్తారా?";

// --- provider connection info ---------------------------------------------
// base = direct native endpoint (OpenAI-compatible /chat/completions for all
// of these). gwPrefix = Vercel AI Gateway model namespace (null = not on GW).
export const PROVIDERS = {
  openai:   { key: "OPENAI_API_KEY",   base: "https://api.openai.com/v1",                                gwPrefix: "openai/" },
  google:   { key: "GEMINI_API_KEY",   base: "https://generativelanguage.googleapis.com/v1beta/openai",  gwPrefix: "google/" },
  deepseek: { key: "DEEPSEEK_API_KEY", base: "https://api.deepseek.com",                                  gwPrefix: "deepseek/" },
  groq:     { key: "GROQ_API_KEY",     base: "https://api.groq.com/openai/v1",                            gwPrefix: null },
  sarvam:   { key: "SARVAM_API_KEY",   base: "https://api.sarvam.ai/v1",                                  gwPrefix: null },
};

// --- the matrix: all providers, all candidate models ----------------------
// IDs verified live against each provider's /models endpoint (2026-06).
export const MODELS = [
  // OpenAI: GPT-4.x (non-reasoning) + GPT-5 *-chat-latest (non-reasoning) + gpt-5.4-mini (reasoner)
  { provider: "openai", model: "gpt-4o-mini" },
  { provider: "openai", model: "gpt-4.1-mini" },
  { provider: "openai", model: "gpt-4.1-nano" },
  { provider: "openai", model: "gpt-4.1" },
  { provider: "openai", model: "gpt-5-chat-latest" },
  { provider: "openai", model: "gpt-5.1-chat-latest" },
  { provider: "openai", model: "gpt-5.2-chat-latest" },
  { provider: "openai", model: "gpt-5.4-mini" },

  // Gemini (thinking disabled)
  { provider: "google", model: "gemini-2.5-flash" },
  { provider: "google", model: "gemini-2.5-flash-lite" },
  { provider: "google", model: "gemini-3-flash-preview" },
  { provider: "google", model: "gemini-3.1-flash-lite" },
  { provider: "google", model: "gemini-3.5-flash" },

  // DeepSeek v4 (thinking disabled — default is ON)
  { provider: "deepseek", model: "deepseek-v4-flash" },
  { provider: "deepseek", model: "deepseek-v4-pro" },

  // Groq (LPU; reasoning disabled where applicable). Not on Vercel gateway.
  { provider: "groq", model: "llama-3.3-70b-versatile" },
  { provider: "groq", model: "llama-3.1-8b-instant" },
  { provider: "groq", model: "meta-llama/llama-4-scout-17b-16e-instruct" },
  { provider: "groq", model: "qwen/qwen3.6-27b" },
  { provider: "groq", model: "openai/gpt-oss-20b" },

  // Sarvam (Indian-language specialist — strong Telugu). Direct/BYOK only.
  { provider: "sarvam", model: "sarvam-30b" },
  { provider: "sarvam", model: "sarvam-105b" },
];

// Reasoners that need an explicit reasoning_effort to stop thinking. Everything
// else (gpt-4*, gpt-5*-chat-latest, Groq llama*, sarvam) is already
// non-reasoning and REJECTS a reasoning_effort field — so send nothing.
const OPENAI_REASONER = /^gpt-5[\d.]*-mini|^o\d/;

// "min reasoning" value per reasoner family (validated against live 400s):
//   openai gpt-5*-mini : "none"  ('minimal' rejected)
//   groq   qwen3       : "none"
//   groq   gpt-oss     : "low"   (none/minimal rejected — low is the floor)
function reasoningEffort(provider, model) {
  if (provider === "openai" && OPENAI_REASONER.test(model)) return "none";
  if (provider === "groq" && /gpt-oss/.test(model)) return "low";
  if (provider === "groq" && /qwen3/.test(model)) return "none";
  return null; // no reasoning param at all
}

// Raw OpenAI-compatible body fields that disable thinking, per provider.
export function rawReasoningOff(provider, model) {
  if (provider === "deepseek") return { thinking: { type: "disabled" } };
  if (provider === "google") return { reasoning_effort: "none" };
  const eff = reasoningEffort(provider, model);
  return eff ? { reasoning_effort: eff } : {};
}

// Vercel AI SDK providerOptions that disable thinking, per provider.
export function sdkReasoningOff(provider, model) {
  if (provider === "google") {
    // Gemini 2.5 disables thinking with thinkingBudget:0; Gemini 3.x ignores the
    // budget and uses thinkingLevel instead ("low" is the floor — 3.x can't go to 0).
    const cfg = /gemini-3/.test(model)
      ? { thinkingLevel: "low", includeThoughts: false }
      : { thinkingBudget: 0, includeThoughts: false };
    return { google: { thinkingConfig: cfg } };
  }
  if (provider === "deepseek") return { deepseek: { thinking: { type: "disabled" } } };
  const eff = reasoningEffort(provider, model);
  return eff ? { [provider]: { reasoningEffort: eff } } : {};
}

// GPT-5 family rejects max_tokens; wants max_completion_tokens.
export const needsCompletionTokens = (model) => /(^|\/)gpt-5/.test(model);

// --- stats + reporting ----------------------------------------------------
export const median = (a) => {
  const s = [...a].sort((x, y) => x - y);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
};

// Run `fn` (returns {ttft_ms,total_ms,chars,usageOut}) RUNS times, median it.
// `skip` short-circuits with a SKIP row (e.g. missing key / not on gateway).
export async function benchModel(label, fn, skip) {
  if (skip) return { label, status: skip };
  const ttfts = [], totals = [];
  let outTok = null, chars = 0;
  for (let i = 0; i < RUNS; i++) {
    try {
      const r = await fn();
      ttfts.push(r.ttft_ms);
      totals.push(r.total_ms);
      outTok = r.usageOut ?? outTok;
      chars = r.chars;
    } catch (e) {
      return { label, status: `ERR: ${String(e.message || e).slice(0, 160)}` };
    }
  }
  return { label, ttft: median(ttfts), total: median(totals), outTok: outTok ?? `~${chars}c` };
}

export function report(title, rows) {
  console.log(`\n=== ${title} — SUMMARY (median, sorted by TTFT) ===`);
  const ok = rows.filter((r) => r.ttft != null).sort((a, b) => a.ttft - b.ttft);
  const bad = rows.filter((r) => r.ttft == null);
  const pad = (s, n) => String(s).padEnd(n);
  console.log(pad("MODEL", 40), pad("TTFT", 9), pad("TOTAL", 9), "OUT TOK");
  for (const r of ok)
    console.log(pad(r.label, 40), pad(`${(r.ttft / 1000).toFixed(2)}s`, 9), pad(`${(r.total / 1000).toFixed(2)}s`, 9), r.outTok);
  for (const r of bad) console.log(pad(r.label, 40), r.status);
  console.log("");
}

export function header(title) {
  console.log(`\n${title} — ${RUNS} run(s)/model, max_tokens=${MAX_TOKENS}, thinking=OFF`);
  console.log(`System prompt: ${SYSTEM_PROMPT.length} chars | User: "${USER_QUERY}"\n`);
}
