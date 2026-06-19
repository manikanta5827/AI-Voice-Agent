// G.711 Mu-law decode table (Twilio audio → PCM S16LE for Sarvam STT)
const MU_LAW_DECODE_TABLE = new Int16Array(256);
for (let i = 0; i < 256; i++) {
  const mu = ~i;
  const sign = mu & 0x80;
  const exponent = (mu & 0x70) >> 4;
  const mantissa = mu & 0x0f;
  let sample = ((mantissa << 3) + 132) << exponent;
  sample -= 132;
  MU_LAW_DECODE_TABLE[i] = sign ? -sample : sample;
}

function decodeMuLawToPCM(mulawBuffer: Buffer): Buffer {
  const pcm = Buffer.alloc(mulawBuffer.length * 2);
  for (let i = 0; i < mulawBuffer.length; i++) {
    pcm.writeInt16LE(MU_LAW_DECODE_TABLE[mulawBuffer[i]!]!, i * 2);
  }
  return pcm;
}

// Wrap raw PCM S16LE in a minimal WAV container (44-byte header).
// For streaming, we use 0xFFFFFFFF for the sizes so the decoder reads until EOF.
function pcmToWav(pcm: Buffer, sampleRate = 8000, channels = 1, bitsPerSample = 16): Buffer {
  const header = Buffer.alloc(44);
  header.write("RIFF", 0);
  header.writeUInt32LE(0xFFFFFFFF, 4);   // dummy size for streaming
  header.write("WAVE", 8);
  header.write("fmt ", 12);
  header.writeUInt32LE(16, 16);          // PCM chunk size
  header.writeUInt16LE(1, 20);           // PCM format
  header.writeUInt16LE(channels, 22);
  header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(sampleRate * channels * (bitsPerSample / 8), 28); // byte rate
  header.writeUInt16LE(channels * (bitsPerSample / 8), 32);              // block align
  header.writeUInt16LE(bitsPerSample, 34);
  header.write("data", 36);
  header.writeUInt32LE(0xFFFFFFFF, 40);  // dummy size for streaming
  return Buffer.concat([header, pcm]);
}

/**
 * Persistent WebSocket connection to Sarvam STT.
 * Streams decoded PCM chunks in, fires onFinalTranscript when VAD detects end-of-speech.
 */
export class SarvamSTT {
  private ws: WebSocket | null = null;
  private currentTranscript = "";
  private isSpeechEnded = false;
  private readonly apiKey: string;
  private stopped = false;
  private connecting = false; // gate: prevents flood of connect() from sendChunk while ws=null

  onFinalTranscript: ((text: string) => void) | null = null;
  onError: ((err: Error) => void) | null = null;

  constructor() {
    this.apiKey = Bun.env.SARVAM_API_KEY || "";
    if (!this.apiKey) console.warn("⚠️ SARVAM_API_KEY not set");
  }

  async connect(): Promise<void> {
    const params = new URLSearchParams({
      "language-code": "te-IN",
      model: "saaras:v3",
      mode: "transcribe",
      sample_rate: "8000",
      input_audio_codec: "wav",
      vad_signals: "true",
      with_timestamps: "false",
    });

    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(`wss://api.sarvam.ai/speech-to-text/ws?${params}`, {
        headers: { "Api-Subscription-Key": this.apiKey },
      } as any);

      this.ws.onopen = () => {
        this.connecting = false;
        this.hasSentHeader = false;
        console.log("🎙️ STT: WebSocket connected");
        resolve();
      };

      this.ws.onmessage = (event: MessageEvent) => {
        try {
          console.log("🎙️ STT raw:", event.data); // temp: diagnose message structure
          const msg = JSON.parse(event.data as string) as {
            type: string;
            data?: { transcript?: string; signal_type?: string };
          };

          if (msg.type === "data" && msg.data?.transcript) {
            const transcript = msg.data.transcript.trim();
            if (this.isSpeechEnded) {
              // END_SPEECH was already received, so this is the final transcript
              this.isSpeechEnded = false;
              this.currentTranscript = "";
              console.log(`🎙️ STT: Final transcript received after END_SPEECH — "${transcript}"`);
              if (transcript) this.onFinalTranscript?.(transcript);
            } else {
              // Intermediate transcript
              this.currentTranscript = transcript;
            }
          } else if (msg.type === "events") {
            if (msg.data?.signal_type === "START_SPEECH") {
              this.currentTranscript = "";
              this.isSpeechEnded = false;
              console.log("🎙️ STT: Speech start");
            } else if (msg.data?.signal_type === "END_SPEECH") {
              console.log(`🎙️ STT: END_SPEECH event received. Current buffer: "${this.currentTranscript}"`);
              if (this.currentTranscript) {
                // Transcript arrived before END_SPEECH, finalize now
                const text = this.currentTranscript;
                this.currentTranscript = "";
                this.isSpeechEnded = false;
                console.log(`🎙️ STT: Finalizing with existing transcript — "${text}"`);
                this.onFinalTranscript?.(text);
              } else {
                // Transcript has not arrived yet, wait for the data message
                this.isSpeechEnded = true;
                console.log(`🎙️ STT: Waiting for final transcript message...`);
              }
            }
          }
        } catch (e) {
          console.error("❌ STT: Message parse error", e);
        }
      };

      this.ws.onerror = (e: Event) => {
        this.connecting = false;
        const err = new Error(`STT WebSocket error: ${e}`);
        console.error("❌ STT: WebSocket error", e);
        this.onError?.(err);
        reject(err);
      };

      this.ws.onclose = (event: CloseEvent) => {
        this.connecting = false;
        // Log close code so we can diagnose Sarvam rejection reason
        console.log(`🎙️ STT: WebSocket closed — code: ${event.code}, reason: "${event.reason}"`);
        this.ws = null;
      };
    });
  }

  // Accepts raw Twilio MULAW buffer, decodes to PCM S16LE, sends JSON to Sarvam STT.
  // Lazy-connects on first chunk (Sarvam drops idle connections with no audio).
  // `connecting` flag prevents re-entrant connect() flood from Twilio's 50 chunks/sec.
  sendChunk(mulawBuffer: Buffer): void {
    if (this.stopped || this.connecting) return;

    if (!this.ws || this.ws.readyState === WebSocket.CLOSED || this.ws.readyState === WebSocket.CLOSING) {
      this.connecting = true;
      this.connect()
        .then(() => {
          if (this.ws?.readyState === WebSocket.OPEN) {
            this._send(mulawBuffer);
          }
        })
        .catch(e => this.onError?.(e));
      return;
    }

    if (this.ws.readyState !== WebSocket.OPEN) return;
    this._send(mulawBuffer);
  }

  private hasSentHeader = false;

  private _send(mulawBuffer: Buffer): void {
    const pcm = decodeMuLawToPCM(mulawBuffer);
    
    let chunkToSend = pcm;
    if (!this.hasSentHeader) {
      chunkToSend = pcmToWav(pcm);
      this.hasSentHeader = true;
    }

    this.ws!.send(JSON.stringify({
      audio: {
        data: chunkToSend.toString("base64"),
        encoding: "audio/wav",
        sample_rate: "8000",
      },
    }));
  }

  disconnect(): void {
    this.stopped = true;
    this.ws?.close();
    this.ws = null;
  }
}
