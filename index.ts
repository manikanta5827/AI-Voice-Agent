import Fastify from "fastify";
import formbody from "@fastify/formbody";
import websocket from "@fastify/websocket";
import twilio from "twilio";
import { transcribeAudio } from "./stt.ts";
import { getLLMResponseStream } from "./llm.ts";
import { textToSpeech } from "./tts.ts";
import type { ModelMessage } from "ai";

const fastify = Fastify({ logger: true });

await fastify.register(formbody);
await fastify.register(websocket);

// Health check endpoint
fastify.get("/", async () => {
  return { status: "running" };
});

// Trigger an outbound call using Twilio REST API
fastify.get("/make-call", async (req, reply) => {
  console.log("📞 Outbound Call Request received");

  const accountSid = Bun.env.TWILIO_ACCOUNT_SID;
  const authToken = Bun.env.TWILIO_AUTH_TOKEN;
  const fromNumber = Bun.env.TWILIO_US_NUMBER;
  const toNumber = Bun.env.MY_INDIAN_NUMBER;
  const publicUrl = Bun.env.PUBLIC_URL;

  if (!accountSid || !authToken || !fromNumber || !toNumber || !publicUrl) {
    console.error("❌ Configuration Error: Missing required Twilio or URL environment variables.");
    reply.status(500);
    return { error: "Missing Twilio credentials or numbers in configuration" };
  }

  try {
    const client = twilio(accountSid, authToken);
    const call = await client.calls.create({
      url: `https://${publicUrl}/incoming-call`,
      to: toNumber,
      from: fromNumber,
    });

    console.log(`📞 Outbound call successfully initiated. SID: ${call.sid}`);
    return { status: "calling", sid: call.sid };
  } catch (error) {
    console.error("❌ Twilio REST API Error: Failed to create call", error);
    reply.status(500);
    return { error: "Failed to initiate outbound call", details: error instanceof Error ? error.message : String(error) };
  }
});

// Twilio webhook for incoming calls
fastify.post("/incoming-call", async (req, reply) => {
  console.log("📞 Incoming call webhook triggered from Twilio");

  const publicUrl = Bun.env.PUBLIC_URL;
  if (!publicUrl) {
    console.error("❌ Configuration Error: PUBLIC_URL environment variable is missing.");
    reply.status(500);
    return "PUBLIC_URL is not set";
  }

  reply.type("text/xml");
  return `<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say language="te-IN">నమస్కారం, నేను మీకు ఒక ముఖ్యమైన విషయం చెప్పాలనుకుంటున్నాను</Say>
  <Connect>
    <Stream url="wss://${publicUrl}/media-stream" />
  </Connect>
</Response>`;
});

// Twilio WebSocket Media Stream handler
fastify.register(async (fastifyInstance) => {
  fastifyInstance.get("/media-stream", { websocket: true }, (socket, req) => {
    console.log("🎙️ WebSocket connected — audio stream live!");

    let streamSid = "";
    let callSid = "";
    let audioBuffer: Buffer[] = [];
    let audioBufferSize = 0;
    let isProcessing = false;
    let isSpeaking = false;           // true only while audio chunks are being sent to Twilio
    let currentAbort: AbortController | null = null;
    let silenceTimeout: Timer | null = null;
    let inactivityTimeout: Timer | null = null;
    const conversationHistory: ModelMessage[] = [];

    // 30s of total silence (user not speaking at all) → hang up
    const INACTIVITY_MS = 30_000;

    const clearAllTimers = () => {
      if (silenceTimeout) { clearTimeout(silenceTimeout); silenceTimeout = null; }
      if (inactivityTimeout) { clearTimeout(inactivityTimeout); inactivityTimeout = null; }
    };

    const resetInactivityTimer = () => {
      if (inactivityTimeout) clearTimeout(inactivityTimeout);
      inactivityTimeout = setTimeout(async () => {
        if (isProcessing) return; // agent busy — don't cut mid-response
        console.log("📵 30s inactivity — ending call");
        try {
          const client = twilio(Bun.env.TWILIO_ACCOUNT_SID!, Bun.env.TWILIO_AUTH_TOKEN!);
          await client.calls(callSid).update({ status: "completed" });
        } catch (e) {
          console.error("❌ Failed to end call via REST, closing WebSocket", e);
          socket.close();
        }
      }, INACTIVITY_MS);
    };

    // Splits on Telugu/English sentence boundaries; returns complete sentences + leftover.
    const extractSentences = (text: string): { sentences: string[]; remainder: string } => {
      const re = /[.!?।]+\s*/g;
      const sentences: string[] = [];
      let lastIndex = 0;
      let match: RegExpExecArray | null;
      while ((match = re.exec(text)) !== null) {
        const s = text.slice(lastIndex, match.index + match[0].length).trim();
        if (s) sentences.push(s);
        lastIndex = match.index + match[0].length;
      }
      return { sentences, remainder: text.slice(lastIndex) };
    };

    const processAudioBuffer = async () => {
      if (isProcessing || audioBufferSize === 0) return;
      isProcessing = true;

      if (silenceTimeout) { clearTimeout(silenceTimeout); silenceTimeout = null; }

      const inputAudio = Buffer.concat(audioBuffer);
      audioBuffer = [];
      audioBufferSize = 0;

      const abort = new AbortController();
      currentAbort = abort;

      try {
        // 1. STT — must wait for full audio clip
        const transcript = await transcribeAudio(inputAudio);
        if (!transcript.trim()) {
          console.log("🎙️ STT: Empty transcript, ignoring.");
          isProcessing = false;
          currentAbort = null;
          resetInactivityTimer();
          return;
        }

        // Push user message to history
        conversationHistory.push({ role: "user", content: transcript });

        // 2. Stream LLM → fire TTS per sentence as text arrives (parallel TTS calls)
        let textBuffer = "";
        const audioPromises: Promise<string>[] = [];
        let fullLLMResponse = "";

        const result = await getLLMResponseStream(conversationHistory);

        for await (const chunk of result.textStream) {
          if (abort.signal.aborted) break;
          textBuffer += chunk;
          fullLLMResponse += chunk;
          const { sentences, remainder } = extractSentences(textBuffer);
          textBuffer = remainder;
          for (const sentence of sentences) {
            // ponytail: TTS calls start immediately as sentences arrive — parallel, not serial
            audioPromises.push(textToSpeech(sentence));
          }
        }

        if (!abort.signal.aborted && textBuffer.trim()) {
          const finalSentence = textBuffer.trim();
          fullLLMResponse += finalSentence;
          audioPromises.push(textToSpeech(finalSentence));
        }

        // Append assistant response to history
        if (fullLLMResponse.trim()) {
          conversationHistory.push({ role: "assistant", content: fullLLMResponse });
        }

        // 3. Drain TTS results in order → send each sentence's audio to Twilio as it's ready
        let totalPlaybackMs = 0;
        for (const audioPromise of audioPromises) {
          if (abort.signal.aborted) break;
          const base64Mulaw = await audioPromise;
          if (abort.signal.aborted || !streamSid) break;
          socket.send(JSON.stringify({ event: "media", streamSid, media: { payload: base64Mulaw } }));
          isSpeaking = true;
          totalPlaybackMs += (Buffer.from(base64Mulaw, "base64").length / 8000) * 1000;
        }

        if (abort.signal.aborted) {
          // Barge-in already cleared Twilio buffer; just reset state
          isSpeaking = false;
          isProcessing = false;
          currentAbort = null;
          return;
        }

        if (streamSid && totalPlaybackMs > 0) {
          console.log(`🔊 TTS: Total playback estimated: ${totalPlaybackMs.toFixed(0)}ms`);
          setTimeout(() => {
            isSpeaking = false;
            isProcessing = false;
            currentAbort = null;
            resetInactivityTimer();
            console.log("🎙️ Ready for next user input");
          }, totalPlaybackMs + 500);
        } else {
          isSpeaking = false;
          isProcessing = false;
          currentAbort = null;
          resetInactivityTimer();
        }
      } catch (error) {
        console.error("❌ Pipeline Error:", error);
        isSpeaking = false;
        isProcessing = false;
        currentAbort = null;
        resetInactivityTimer();
      }
    };

    socket.on("message", (rawMsg: any) => {
      try {
        const msg = JSON.parse(rawMsg.toString());

        switch (msg.event) {
          case "connected":
            console.log("✅ Media stream connected");
            break;

          case "start":
            streamSid = msg.start?.streamSid || "";
            callSid = msg.start?.callSid || "";
            console.log("▶️ Stream started, Stream SID:", streamSid);
            resetInactivityTimer();
            break;

          case "media":
            if (isProcessing) {
              if (isSpeaking) {
                // Barge-in: user spoke while agent audio was playing → stop agent, listen to user
                console.log("🛑 Barge-in — clearing agent audio, switching to user");
                currentAbort?.abort();
                socket.send(JSON.stringify({ event: "clear", streamSid }));
                isSpeaking = false;
                isProcessing = false;
                currentAbort = null;
                // fall through — collect this chunk as start of user's new utterance
              } else {
                // Still in STT/LLM phase (not speaking yet) — discard
                return;
              }
            }

            resetInactivityTimer();
            const chunk = Buffer.from(msg.media.payload, "base64");
            audioBuffer.push(chunk);
            audioBufferSize += chunk.length;

            // Reset VAD silence timer — process only after 1.5s of silence (end of utterance)
            if (silenceTimeout) { clearTimeout(silenceTimeout); silenceTimeout = null; }
            silenceTimeout = setTimeout(processAudioBuffer, 1500);
            break;

          case "stop":
            console.log("⏹️ Call ended / Stream stopped");
            clearAllTimers();
            break;
        }
      } catch (err) {
        console.error("❌ WebSocket message error:", err);
      }
    });

    socket.on("close", () => {
      console.log("🔌 WebSocket closed");
      clearAllTimers();
    });

    socket.on("error", (err: Error) => {
      console.error("❌ WebSocket error:", err);
      clearAllTimers();
    });
  });
});

const PORT = Bun.env.PORT ? parseInt(Bun.env.PORT) : 8080;
await fastify.listen({ port: PORT, host: "0.0.0.0" });
console.log(`🚀 Server running on port ${PORT}`);