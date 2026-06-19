/**
 * Persistent WebSocket connection to Sarvam TTS.
 * Accepts text sentences, fires onAudioChunk with MULAW bytes for Twilio.
 * Fires onComplete after synthesis of all queued text finishes.
 */
export class SarvamTTS {
  private ws: WebSocket | null = null;
  private readonly apiKey: string;
  private _totalMulawBytes = 0;

  onAudioChunk: ((mulawBuffer: Buffer) => void) | null = null;
  onComplete: (() => void) | null = null;

  constructor() {
    this.apiKey = Bun.env.SARVAM_API_KEY || "";
    if (!this.apiKey) console.warn("⚠️ SARVAM_API_KEY not set");
  }

  get totalMulawBytes(): number {
    return this._totalMulawBytes;
  }

  resetByteCount(): void {
    this._totalMulawBytes = 0;
  }

  async connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(
        "wss://api.sarvam.ai/text-to-speech/ws?model=bulbul:v3",
        { headers: { "api-subscription-key": this.apiKey } } as any
      );

      this.ws.onopen = () => {
        // Only send documented config fields — unknown fields cause server to reject + close
        this.ws!.send(
          JSON.stringify({
            type: "config",
            data: {
              target_language_code: "te-IN",
              speaker: "ratan",
              speech_sample_rate: 8000,
              output_audio_codec: "mulaw", // request MULAW directly, no encode step needed
              pace: 1.0,
              model: "bulbul:v3",
              send_completion_event: true,
            },
          })
        );
        console.log("🔊 TTS: WebSocket connected");
        resolve();
      };

      this.ws.onmessage = (event: MessageEvent) => {
        try {
          const msg = JSON.parse(event.data as string) as {
            type: string;
            data?: { audio?: string; event_type?: string };
          };

          if (msg.type === "audio" && msg.data?.audio) {
            // Sarvam returns MULAW directly — forward as-is, no encode step
            const mulaw = Buffer.from(msg.data.audio, "base64");
            this._totalMulawBytes += mulaw.length;
            this.onAudioChunk?.(mulaw);
          } else if (msg.type === "event" && msg.data?.event_type === "final") {
            console.log(`🔊 TTS: Synthesis complete — ${this._totalMulawBytes} MULAW bytes`);
            this.onComplete?.();
          }
        } catch (e) {
          console.error("❌ TTS: Message parse error", e);
        }
      };

      this.ws.onerror = (e: Event) => {
        console.error("❌ TTS: WebSocket error", e);
        reject(new Error(`TTS WebSocket error: ${e}`));
      };

      this.ws.onclose = () => {
        console.log("🔊 TTS: WebSocket closed");
        this.ws = null;
      };
    });
  }

  sendText(text: string): void {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify({ type: "text", data: { text } }));
  }

  // Signal end of text stream so Sarvam flushes remaining buffer and fires final event
  flush(): void {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify({ type: "flush" }));
  }

  disconnect(): void {
    this.ws?.close();
    this.ws = null;
  }
}
