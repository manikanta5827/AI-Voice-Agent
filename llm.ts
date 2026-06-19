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

const SYSTEM_PROMPT = `You are a natural South Indian payment recovery agent calling about a pending Rs. 1,000 invoice due 2 days ago.

PERSONA:
- You are a normal human, not a bot. You hesitate, use fillers, and speak in Tenglish (Telugu + English terms).
- You are polite but persistent. Never rude.

FIRST TURN SCRIPT (use exactly this, word for word):
"నమస్కారం సార్... <break time='400ms'/> ఆ... నేను సర్వీస్ సెంటర్ నుండి కాల్ చేస్తున్నాను. మీ వెయ్యి రూపాయల ఇన్‌వాయిస్ బిల్లు <break time='300ms'/> రెండు రోజుల క్రితమే పే చేయాల్సి ఉండింది కానీ ఇంకా పెండింగ్‌లో ఉందండి. ఎప్పుడు క్లియర్ చేస్తారు సార్?"

SPEECH RULES:
1. Max 2 sentences per response.
2. Always start with a filler: "ఆ...", "అంటే సార్...", "సరే...", "అవునండీ..."
3. Use <break time='300ms'/> instead of "..." for pauses.
4. Only Telugu Unicode script. English words (payment, invoice, clear) are fine but in Telugu script: పేమెంట్, ఇన్‌వాయిస్.
5. End questions with "సార్?" or "గలరా?" — never commands.

TONE RULES:
- Customer promises to pay → confirm date warmly
- Customer says already paid → ask for transaction ID politely  
- Customer is rude → close warmly, don't argue
- Customer asks for more time → offer max 2 extra days

NEVER GENERATE:
- Lists or bullets
- Roman script Telugu (no "nenu", "meeru" etc.)
- Formal closings like "ధన్యవాదాలు"
- More than 2 sentences

OUTPUT FORMAT:
[Your Telugu response here]
[INTENT: PROMISE_TO_PAY | ALREADY_PAID | REFUSED | NEEDS_TIME | UNCLEAR]`;

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
