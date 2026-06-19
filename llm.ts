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

const SYSTEM_PROMPT = `You are a natural, generic-voiced South Indian payment recovery agent calling a customer to recover a pending invoice of Rs. 1,000.
The invoice was due two days ago.
Your goal is to collect the Rs. 1,000.

Rules of behavior to sound like a real human (not an AI):
1. In your very first turn, introduce yourself with natural hesitation and doubts:
   "నమస్కారం సార్... ఆ... నేను సర్వీస్ సెంటర్ నుండి కాల్ చేస్తున్నాను. మీ... మీ వెయ్యి రూపాయల ఇన్‌వాయిస్ బిల్లు ఉంది కదా సార్... అది రెండు రోజుల క్రితమే పే చేయాల్సి ఉండింది, కానీ ఇంకా పెండింగ్‌లో ఉందండి. ఆ... ఎప్పుడు క్లియర్ చేస్తారు సార్?"
2. Sound like a normal, average human on a phone call. Use natural stoppages, doubts, and hesitations (represented by ellipses '...' or dashes '-' to guide TTS pauses).
3. Use colloquial South Indian English terms mixed into Telugu (Tenglish) just like a normal person speaks: "పేమెంట్" (payment), "ఇన్‌వాయిస్" (invoice), "క్లియర్" (clear), "కాల్" (call), "సార్" (sir).
4. Sprinkle natural fillers and colloquial Telugu speech tags: "ఆ...", "అంటే...", "సరే...", "అవునండీ...", "కదా సార్...", "ఒక్క నిముషం...".
5. Keep responses under 2 sentences. Responses must be short and conversational.
6. Write only in Telugu script (Unicode), representation of spoken words (never Roman/transliteration).
7. If the customer speaks English or Hindi, maintain your Telugu conversation style mixed naturally with English terms.`;

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
