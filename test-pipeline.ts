import { SarvamSTT } from "./stt.ts";
import { SarvamTTS } from "./tts.ts";
import { getLLMResponseStream } from "./llm.ts";

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

async function runPipelineTest() {
  console.log("🚀 Starting Offline Voice Pipeline Test...");

  const inputTeluguText = "నేను రేపు పేమెంట్ చేస్తాను సార్";
  console.log(`📝 1. Input Telugu Text: "${inputTeluguText}"`);

  // --- Step A: Convert input text to Mu-Law Audio ---
  console.log("🔊 2. Generating audio from input text using Sarvam TTS...");
  const tts1 = new SarvamTTS();
  await tts1.connect();
  
  // Give the server 500ms to initialize after opening the connection
  await new Promise((resolve) => setTimeout(resolve, 500));

  const mulawChunks: Buffer[] = [];
  let tts1CompletedResolver: () => void;
  let tts1Timeout: Timer;

  const tts1CompletedPromise = new Promise<void>((resolve) => {
    tts1CompletedResolver = resolve;
    // 5-second fallback timeout so the script never hangs
    tts1Timeout = setTimeout(() => {
      console.log("⚠️ TTS 1: 5-second fallback timeout reached. Proceeding with collected chunks...");
      resolve();
    }, 5000);
  });

  tts1.onAudioChunk = (chunk) => {
    mulawChunks.push(chunk);
  };

  tts1.onComplete = () => {
    console.log(`🔊 TTS 1: Synthesis finished event received. Generated ${mulawChunks.length} audio chunks.`);
    clearTimeout(tts1Timeout);
    tts1CompletedResolver();
  };

  tts1.sendText(inputTeluguText);
  // Give it a tiny moment before flush
  await new Promise((resolve) => setTimeout(resolve, 200));
  tts1.flush();

  await tts1CompletedPromise;
  tts1.disconnect();

  const fullMulawBuffer = Buffer.concat(mulawChunks);
  console.log(`🔊 Total audio size: ${fullMulawBuffer.length} bytes of 8kHz Mu-law.`);

  if (fullMulawBuffer.length === 0) {
    throw new Error("❌ TTS generated 0 bytes of audio. Can't test STT.");
  }

  // Save the input audio to file
  await Bun.write("input_tts.mulaw", fullMulawBuffer);
  console.log("💾 Saved input audio to `./input_tts.mulaw`");

  // --- Step B: Feed Mu-Law Audio to STT ---
  console.log("🎙️ 3. Initializing STT and streaming audio...");
  const stt = new SarvamSTT();
  await stt.connect();

  let pipelineCompleteResolver: () => void;
  const pipelineCompletePromise = new Promise<void>((resolve) => {
    pipelineCompleteResolver = resolve;
  });

  stt.onFinalTranscript = async (transcript: string) => {
    console.log(`🎙️ STT transcription received: "${transcript}"`);
    
    // --- Step C: Feed transcript to LLM & stream response ---
    console.log("🤖 4. Sending transcript to LLM...");
    try {
      const tts2 = new SarvamTTS();
      await tts2.connect();
      await new Promise((resolve) => setTimeout(resolve, 500));

      const tts2Chunks: Buffer[] = [];
      let tts2CompletedResolver: () => void;
      let tts2Timeout: Timer;

      const tts2CompletedPromise = new Promise<void>((resolve) => {
        tts2CompletedResolver = resolve;
        // 7-second fallback timeout
        tts2Timeout = setTimeout(() => {
          console.log("⚠️ TTS 2: 7-second fallback timeout reached. Proceeding to save...");
          resolve();
        }, 7000);
      });

      tts2.onAudioChunk = (chunk) => {
        tts2Chunks.push(chunk);
      };

      tts2.onComplete = () => {
        console.log(`🔊 TTS 2: Synthesis finished event received.`);
        clearTimeout(tts2Timeout);
        tts2CompletedResolver();
      };

      const conversationHistory = [{ role: "user" as const, content: transcript }];
      const result = await getLLMResponseStream(conversationHistory);

      let textBuffer = "";
      let fullResponse = "";
      console.log("🤖 LLM Response Streaming: ");
      
      for await (const chunk of result.textStream) {
        process.stdout.write(chunk);
        textBuffer += chunk;
        fullResponse += chunk;
        
        const { sentences, remainder } = extractSentences(textBuffer);
        textBuffer = remainder;
        for (const s of sentences) {
          const cleaned = cleanTextForTTS(s);
          if (cleaned) tts2.sendText(cleaned);
        }
      }

      if (textBuffer.trim()) {
        const cleaned = cleanTextForTTS(textBuffer.trim());
        if (cleaned) tts2.sendText(cleaned);
        fullResponse += textBuffer.trim();
      }
      
      console.log("\n🤖 LLM complete response generated.");
      await new Promise((resolve) => setTimeout(resolve, 200));
      tts2.flush();

      // Wait for output synthesis to complete
      await tts2CompletedPromise;
      tts2.disconnect();

      const responseBuffer = Buffer.concat(tts2Chunks);
      console.log(`🔊 Synthesized response: ${responseBuffer.length} bytes of audio.`);
      if (responseBuffer.length > 0) {
        await Bun.write("response_tts.mulaw", responseBuffer);
        console.log("💾 Saved response audio to `./response_tts.mulaw`");
      } else {
        console.log("⚠️ No response audio was synthesized.");
      }

      pipelineCompleteResolver();
    } catch (e) {
      console.error("❌ Error in LLM/TTS2 phase:", e);
      pipelineCompleteResolver();
    }
  };

  stt.onError = (err) => {
    console.error("❌ STT Error:", err);
  };

  // Stream the buffer in chunks of 320 bytes (40ms of 8kHz Mu-law)
  const CHUNK_SIZE = 320;
  console.log("🎙️ Streaming audio chunks to STT...");
  for (let i = 0; i < fullMulawBuffer.length; i += CHUNK_SIZE) {
    const chunk = fullMulawBuffer.subarray(i, i + CHUNK_SIZE);
    stt.sendChunk(chunk);
    await new Promise((resolve) => setTimeout(resolve, 40));
  }

  // Send 1.2 seconds of silence to trigger VAD's END_SPEECH (30 chunks * 40ms)
  console.log("🎙️ Audio stream finished. Sending silence to trigger Voice Activity Detection...");
  const silenceChunk = Buffer.alloc(CHUNK_SIZE, 0xFF); // 0xFF is silence in Mu-law
  for (let i = 0; i < 30; i++) {
    stt.sendChunk(silenceChunk);
    await new Promise((resolve) => setTimeout(resolve, 40));
  }

  // Wait for the pipeline to finish
  await pipelineCompletePromise;
  stt.disconnect();
  console.log("🏁 Pipeline Test Finished Successfully!");
}

runPipelineTest().catch(console.error);
