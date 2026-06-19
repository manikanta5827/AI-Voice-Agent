import Fastify from "fastify";
import formbody from "@fastify/formbody";
import websocket from "@fastify/websocket";
import twilio from "twilio";
import { SarvamSTT } from "./stt.ts";
import { SarvamTTS } from "./tts.ts";
import { getLLMResponseStream } from "./llm.ts";
import type { ModelMessage } from "ai";

const fastify = Fastify({ logger: true });

await fastify.register(formbody);
await fastify.register(websocket);

fastify.get("/", async () => ({ status: "running" }));

// Trigger an outbound call
fastify.get("/make-call", async (req, reply) => {
  console.log("📞 Outbound Call Request received");

  const accountSid = Bun.env.TWILIO_ACCOUNT_SID;
  const authToken = Bun.env.TWILIO_AUTH_TOKEN;
  const fromNumber = Bun.env.TWILIO_US_NUMBER;
  const toNumber = Bun.env.MY_INDIAN_NUMBER;
  const publicUrl = Bun.env.PUBLIC_URL;

  if (!accountSid || !authToken || !fromNumber || !toNumber || !publicUrl) {
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
    reply.status(500);
    return { error: "Failed to initiate outbound call", details: error instanceof Error ? error.message : String(error) };
  }
});

// Twilio webhook — returns TwiML to stream audio
fastify.post("/incoming-call", async (req, reply) => {
  console.log("📞 Incoming call webhook triggered from Twilio");

  const publicUrl = Bun.env.PUBLIC_URL;
  if (!publicUrl) {
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
    let isProcessing = false;
    let isSpeaking = false;
    let currentAbort: AbortController | null = null;
    let inactivityTimeout: Timer | null = null;
    let audioStartTime: number | null = null;
    const conversationHistory: ModelMessage[] = [];

    const stt = new SarvamSTT();
    const tts = new SarvamTTS();

    const INACTIVITY_MS = 30_000;

    const resetInactivityTimer = () => {
      if (inactivityTimeout) clearTimeout(inactivityTimeout);
      inactivityTimeout = setTimeout(async () => {
        if (isProcessing) return;
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

    const cleanup = () => {
      if (inactivityTimeout) { clearTimeout(inactivityTimeout); inactivityTimeout = null; }
      stt.disconnect();
      tts.disconnect();
    };

    // Split text on Telugu/English sentence boundaries
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

    const resetPipelineState = () => {
      isSpeaking = false;
      isProcessing = false;
      currentAbort = null;
      audioStartTime = null;
      tts.onAudioChunk = null;
      tts.onComplete = null;
    };

    const runPipeline = async (transcript: string) => {
      isProcessing = true;
      const abort = new AbortController();
      currentAbort = abort;
      tts.resetByteCount();

      // Wire TTS audio chunks → Twilio (fires for every synthesized chunk)
      tts.onAudioChunk = (mulawBuffer: Buffer) => {
        if (abort.signal.aborted || !streamSid) return;
        if (!audioStartTime) audioStartTime = Date.now();
        socket.send(JSON.stringify({
          event: "media",
          streamSid,
          media: { payload: mulawBuffer.toString("base64") },
        }));
        isSpeaking = true;
      };

      // TTS synthesis complete → wait for playback to finish, then release
      tts.onComplete = () => {
        if (abort.signal.aborted) { resetPipelineState(); resetInactivityTimer(); return; }
        const totalMs = (tts.totalMulawBytes / 8000) * 1000;
        const elapsed = audioStartTime ? Date.now() - audioStartTime : 0;
        const remainingMs = Math.max(totalMs - elapsed, 0);
        console.log(`🔊 TTS: Playback remaining ~${remainingMs.toFixed(0)}ms`);
        setTimeout(() => {
          resetPipelineState();
          resetInactivityTimer();
          console.log("🎙️ Ready for next user input");
        }, remainingMs + 500);
      };

      try {
        conversationHistory.push({ role: "user", content: transcript });

        // Stream LLM → extract sentences → send each to TTS WebSocket as it arrives
        let textBuffer = "";
        let fullResponse = "";
        const result = await getLLMResponseStream(conversationHistory);

        for await (const chunk of result.textStream) {
          if (abort.signal.aborted) break;
          textBuffer += chunk;
          fullResponse += chunk;
          const { sentences, remainder } = extractSentences(textBuffer);
          textBuffer = remainder;
          for (const s of sentences) tts.sendText(s);
        }

        if (!abort.signal.aborted && textBuffer.trim()) {
          tts.sendText(textBuffer.trim());
          fullResponse += textBuffer.trim();
        }

        if (abort.signal.aborted) {
          resetPipelineState();
          resetInactivityTimer();
          return;
        }

        // All LLM text sent — flush TTS to trigger final synthesis + completion event
        tts.flush();

        if (fullResponse.trim()) {
          conversationHistory.push({ role: "assistant", content: fullResponse });
        }
        // onComplete handler above will reset state after playback
      } catch (err) {
        console.error("❌ Pipeline Error:", err);
        resetPipelineState();
        resetInactivityTimer();
      }
    };

    // STT fires this when Sarvam VAD detects end-of-utterance
    stt.onFinalTranscript = (transcript: string) => {
      resetInactivityTimer();

      if (isProcessing) {
        if (isSpeaking) {
          // Barge-in: user started speaking while agent is playing audio
          console.log("🛑 Barge-in (STT) — stopping agent audio");
          currentAbort?.abort();
          socket.send(JSON.stringify({ event: "clear", streamSid }));
          resetPipelineState();
          // Fall through to process new transcript
        } else {
          return; // Agent still in LLM phase — ignore
        }
      }

      runPipeline(transcript);
    };

    stt.onError = (err: Error) => console.error("❌ STT error:", err);

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
            // Connect TTS eagerly (needs to be ready when LLM output arrives).
            // STT connects lazily on first audio chunk — Sarvam drops idle STT connections.
            tts.connect()
              .then(() => {
                console.log("✅ TTS WebSocket ready");
                resetInactivityTimer();
              })
              .catch((e) => console.error("❌ Failed to connect TTS WebSocket:", e));
            break;

          case "media":
            // Always forward audio to STT WebSocket (Sarvam VAD handles speech detection)
            if (isProcessing && isSpeaking) {
              // Twilio-level barge-in detection (backup to STT-level barge-in)
              console.log("🛑 Barge-in (Twilio) — stopping agent audio");
              currentAbort?.abort();
              socket.send(JSON.stringify({ event: "clear", streamSid }));
              resetPipelineState();
            }

            resetInactivityTimer();
            const chunk = Buffer.from(msg.media.payload, "base64");
            stt.sendChunk(chunk); // Stream directly to Sarvam STT — no local buffering
            break;

          case "stop":
            console.log("⏹️ Call ended / Stream stopped");
            cleanup();
            break;
        }
      } catch (err) {
        console.error("❌ WebSocket message error:", err);
      }
    });

    socket.on("close", () => {
      console.log("🔌 WebSocket closed");
      cleanup();
    });

    socket.on("error", (err: Error) => {
      console.error("❌ WebSocket error:", err);
      cleanup();
    });
  });
});

const PORT = Bun.env.PORT ? parseInt(Bun.env.PORT) : 8080;
await fastify.listen({ port: PORT, host: "0.0.0.0" });
console.log(`🚀 Server running on port ${PORT}`);
