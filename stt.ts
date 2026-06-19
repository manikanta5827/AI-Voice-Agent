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

/**
 * Persistent WebSocket connection to Sarvam STT.
 * Streams decoded PCM chunks in, fires onFinalTranscript when VAD detects end-of-speech.
 */
export class SarvamSTT {
  private ws: WebSocket | null = null;
  private currentTranscript = "";
  private readonly apiKey: string;

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
      input_audio_codec: "pcm_s16le",
      vad_signals: "true",
    });

    return new Promise((resolve, reject) => {
      // Bun-specific: headers supported in WebSocket constructor
      this.ws = new WebSocket(`wss://api.sarvam.ai/speech-to-text/ws?${params}`, {
        headers: { "Api-Subscription-Key": this.apiKey },
      } as any);

      this.ws.onopen = () => {
        console.log("🎙️ STT: WebSocket connected");
        resolve();
      };

      this.ws.onmessage = (event: MessageEvent) => {
        try {
          const msg = JSON.parse(event.data as string) as {
            type: string;
            data?: { transcript?: string; signal_type?: string };
          };

          if (msg.type === "data" && msg.data?.transcript) {
            this.currentTranscript = msg.data.transcript.trim();
          } else if (msg.type === "events") {
            if (msg.data?.signal_type === "START_SPEECH") {
              this.currentTranscript = "";
              console.log("🎙️ STT: Speech start");
            } else if (msg.data?.signal_type === "END_SPEECH") {
              const text = this.currentTranscript;
              this.currentTranscript = "";
              console.log(`🎙️ STT: End of speech — "${text}"`);
              if (text) this.onFinalTranscript?.(text);
            }
          }
        } catch (e) {
          console.error("❌ STT: Message parse error", e);
        }
      };

      this.ws.onerror = (e: Event) => {
        const err = new Error(`STT WebSocket error: ${e}`);
        console.error("❌ STT: WebSocket error", e);
        this.onError?.(err);
        reject(err);
      };

      this.ws.onclose = () => {
        console.log("🎙️ STT: WebSocket closed");
        this.ws = null;
      };
    });
  }

  // Accepts raw Twilio MULAW buffer, decodes to PCM S16LE, sends to Sarvam STT
  sendChunk(mulawBuffer: Buffer): void {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    const pcm = decodeMuLawToPCM(mulawBuffer);
    this.ws.send(
      JSON.stringify({
        audio: { data: pcm.toString("base64"), sample_rate: "8000", encoding: "audio/wav" },
      })
    );
  }

  disconnect(): void {
    this.ws?.close();
    this.ws = null;
  }
}
