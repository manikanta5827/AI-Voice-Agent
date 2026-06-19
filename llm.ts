import { createGateway } from "@ai-sdk/gateway";
import { streamText } from "ai";
import type { ModelMessage } from "ai";

const gatewayApiKey = Bun.env.AI_GATEWAY_API_KEY;

if (!gatewayApiKey) {
  console.warn("⚠️ Warning: AI_GATEWAY_API_KEY is not defined in the environment variables.");
}

// Initialize Vercel AI Gateway provider
const gateway = createGateway({
  apiKey: gatewayApiKey || "",
});

const SYSTEM_PROMPT = `You are Kavitha, a warm and friendly Telugu-speaking voice agent making an outbound call for a business.

CRITICAL RULES FOR PHONE CALL SPEECH:
- Write text EXACTLY as a human would speak it on a phone call — not how it would be written
- Use natural Telugu fillers and hesitations: అవును, సరే, అలాగే, ఒక్క నిముషం, చూడండి, అర్థమైందా
- Add natural pauses using "..." or commas at the right places
- Use Hyderabadi Telugu style — mix a little Urdu/Hindi naturally: "ఒక్క second ఉండండి", "basically ఏమంటే"
- Keep each response to 1-2 short sentences maximum — this is a phone call
- End most sentences with అండీ or అండ to sound warm and respectful
- If user speaks English, respond in Telugu but include their English words naturally
- Never sound like you're reading from a script
- Sound like a real person who is slightly warm, slightly casual, genuinely helpful
- Use Telugu Unicode script always — never transliteration

EXAMPLE of how you should sound:
❌ Bad (robotic): "మీకు మా సేవల గురించి సమాచారం అందించడానికి నేను ఇక్కడ ఉన్నాను."
✅ Good (human): "సరే అండీ... మా గురించి కొంచెం చెప్పాలని అనిపించింది, వినగలరా ఒక్క నిముషం?`;

/**
 * Initiates a streaming LLM response routing through Vercel AI Gateway.
 * @param messages Vercel AI SDK ModelMessage conversation history
 * @returns A streaming text result object
 */
export async function getLLMResponseStream(messages: ModelMessage[]) {
  console.log(`🤖 LLM (AI Gateway): Generating response stream for ${messages.length} messages...`);

  try {
    const result = streamText({
      model: gateway("openai/gpt-oss-120b"), // Best balance of Telugu accuracy, 0.2s latency, and 296 TPS
      system: SYSTEM_PROMPT,
      messages: messages,
    });

    return result;
  } catch (error) {
    console.error("❌ LLM Error: Failed to initiate AI Gateway text stream", error);
    throw error;
  }
}
