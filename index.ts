import Fastify from "fastify";
import formbody from "@fastify/formbody";
import websocket from "@fastify/websocket";
import twilio from "twilio";
import { SonioxSTT } from "./stt.ts";
import { CartesiaTTS } from "./tts.ts";
import { getLLMResponseStream } from "./llm.ts";
import type { ModelMessage } from "ai";
import { initDB, insertCall, insertMessage, endCall } from "./db.ts";

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
    let shouldEndAfterResponse = false;
    let idleWarnTimeout: Timer | null = null;
    let idleHangupTimeout: Timer | null = null;
    let maxDurationTimer: Timer | null = null;
    let audioStartTime: number | null = null;
    const conversationHistory: ModelMessage[] = [];

    const stt = new SonioxSTT();
    const tts = new CartesiaTTS();

    // Play a TTS message outside the main pipeline (idle warnings, max-duration, etc.)
    const playIdleMessage = (text: string, onDone?: () => void) => {
      tts.newContext(); // fresh context_id — prior contexts are closed after flush()
      tts.onAudioChunk = (buf: Buffer) => {
        if (!streamSid) return;
        socket.send(JSON.stringify({ event: "media", streamSid, media: { payload: buf.toString("base64") } }));
      };
      tts.onComplete = () => {
        tts.onAudioChunk = null;
        tts.onComplete = null;
        onDone?.();
      };
      tts.sendText(text);
      tts.flush();
    };

    const resetInactivityTimer = () => {
      if (idleWarnTimeout) { clearTimeout(idleWarnTimeout); idleWarnTimeout = null; }
      if (idleHangupTimeout) { clearTimeout(idleHangupTimeout); idleHangupTimeout = null; }

      idleWarnTimeout = setTimeout(() => {
        if (isProcessing) return;
        playIdleMessage("అక్కడ ఉన్నారా sir?", () => {
          idleHangupTimeout = setTimeout(async () => {
            if (isProcessing) return;
            console.log("📵 20s total idle — ending call");
            playIdleMessage("సరే sir, తర్వాత మాట్లాడదాం. Bye!");
            await endCall(callSid).catch(() => {});
            setTimeout(async () => {
              try {
                const client = twilio(Bun.env.TWILIO_ACCOUNT_SID!, Bun.env.TWILIO_AUTH_TOKEN!);
                await client.calls(callSid).update({ status: "completed" });
              } catch { socket.close(); }
            }, 3000);
          }, 10_000);
        });
      }, 10_000);
    };

    const cleanup = () => {
      if (idleWarnTimeout) { clearTimeout(idleWarnTimeout); idleWarnTimeout = null; }
      if (idleHangupTimeout) { clearTimeout(idleHangupTimeout); idleHangupTimeout = null; }
      if (maxDurationTimer) { clearTimeout(maxDurationTimer); maxDurationTimer = null; }
      if (callSid) endCall(callSid).catch(() => {});
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

    // Clean SSML break tags and bracketed metadata before sending to TTS
    const cleanTextForTTS = (text: string): string => {
      return text
        .replace(/<break[^>]*\/>/gi, "...")
        .replace(/<[^>]*>/gi, "")
        .replace(/\[INTENT:[^\]]*\]/gi, "")
        .replace(/\[[^\]]*\]/gi, "")
        .trim();
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
        setTimeout(async () => {
          resetPipelineState();
          if (shouldEndAfterResponse) {
            shouldEndAfterResponse = false;
            console.log("👋 End-call signal — hanging up after farewell");
            await endCall(callSid).catch(() => {});
            try {
              const client = twilio(Bun.env.TWILIO_ACCOUNT_SID!, Bun.env.TWILIO_AUTH_TOKEN!);
              await client.calls(callSid).update({ status: "completed" });
            } catch { socket.close(); }
            return;
          }
          resetInactivityTimer();
          console.log("🎙️ Ready for next user input");
        }, remainingMs + 500);
      };

      try {
        conversationHistory.push({ role: "user", content: transcript });
        insertMessage(callSid, "user", transcript).catch(e => console.error("❌ DB:", e));

        // Stream LLM → extract sentences → send each to TTS WebSocket as it arrives
        let textBuffer = "";
        let fullResponse = "";
        const result = await getLLMResponseStream(conversationHistory, abort.signal);

        for await (const chunk of result.textStream) {
          if (abort.signal.aborted) break;
          textBuffer += chunk;
          fullResponse += chunk;
          const { sentences, remainder } = extractSentences(textBuffer);
          textBuffer = remainder;
          for (const s of sentences) {
            const cleaned = cleanTextForTTS(s);
            if (cleaned) tts.sendText(cleaned);
          }
        }

        if (!abort.signal.aborted && textBuffer.trim()) {
          const cleaned = cleanTextForTTS(textBuffer.trim());
          if (cleaned) tts.sendText(cleaned);
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
          console.log(`🤖 Agent: "${fullResponse.trim()}"`);
          insertMessage(callSid, "assistant", fullResponse.trim()).catch(e => console.error("❌ DB:", e));
          conversationHistory.push({ role: "assistant", content: fullResponse });
        }
        // onComplete handler above will reset state after playback
      } catch (err) {
        console.error("❌ Pipeline Error:", err);
        resetPipelineState();
        resetInactivityTimer();
      }
    };

    stt.onFinalTranscript = (transcript: string) => {
      resetInactivityTimer();

      if (isProcessing) {
        console.log("🛑 Barge-in — interrupting agent");
        currentAbort?.abort();
        if (isSpeaking) socket.send(JSON.stringify({ event: "clear", streamSid }));
        resetPipelineState();
        // Fall through to process new transcript
      }

      console.log(`👤 User: "${transcript}"`);
      const END_SIGNALS = ["bye", "goodbye", "ok bye", "thank you", "thanks", "థాంక్యూ", "అయిపోయింది", "చాలు"];
      if (END_SIGNALS.some(s => transcript.toLowerCase().includes(s))) shouldEndAfterResponse = true;
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
            insertCall(callSid, streamSid).catch(e => console.error("❌ DB insertCall:", e));
            maxDurationTimer = setTimeout(() => {
              console.log("⏰ Max call duration (600s) — ending call");
              playIdleMessage("సరే sir, call time అయిపోయింది. తర్వాత మాట్లాడదాం!", async () => {
                await endCall(callSid).catch(() => {});
                try {
                  const client = twilio(Bun.env.TWILIO_ACCOUNT_SID!, Bun.env.TWILIO_AUTH_TOKEN!);
                  await client.calls(callSid).update({ status: "completed" });
                } catch { socket.close(); }
              });
            }, 600_000);
            // Connect TTS eagerly (needs to be ready when LLM output arrives).
            // STT connects lazily on first audio chunk — Sarvam drops idle STT connections.
            tts.connect()
              .then(() => {
                console.log("✅ TTS WebSocket ready");
                playIdleMessage(
                  "నమస్కారం sir, SecureLife Insurance కి స్వాగతం. మీకు ఏ విషయంలో సహాయం కావాలి?",
                  () => resetInactivityTimer()
                );
              })
              .catch((e) => console.error("❌ Failed to connect TTS WebSocket:", e));
            break;

          case "media":
            resetInactivityTimer();
            const chunk = Buffer.from(msg.media.payload, "base64");
            stt.sendChunk(chunk);
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

await initDB();
const PORT = Bun.env.PORT ? parseInt(Bun.env.PORT) : 8080;
await fastify.listen({ port: PORT, host: "0.0.0.0" });
console.log(`🚀 Server running on port ${PORT}`);
